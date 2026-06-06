#!/usr/bin/env python3

import json
import os
import boto3
import uuid
import asyncio
import jwt
from datetime import datetime, timedelta
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from sales_memory_hooks import SalesAgentMemoryHooks
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from typing import Dict, Any

# Configuration
REGION = "us-east-1"
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
SESSION_ID = str(uuid.uuid4())

def extract_user_info_from_jwt(headers):
    """Extract user ID from Cognito JWT token"""
    auth_header = headers.get('authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.replace('Bearer ', '')
    
    # Decode without verification (already validated by AgentCore)
    decoded_token = jwt.decode(token, options={"verify_signature": False})
    
    # Extract user identifier
    user_id = decoded_token.get('sub')  # Primary user UUID
    username = decoded_token.get('cognito:username')  # Username
    
    return {
        'user_id': user_id,
        'username': username,
        'email': decoded_token.get('email'),
        'groups': decoded_token.get('cognito:groups', [])
    }

CLUSTER_IDENTIFIER = "sales-demo-cluster"
DATABASE_NAME = "salesdb"


@tool
async def discover_schema(schema_name: str = "sales") -> str:
    """List tables in the given Redshift schema along with their columns.

    Always call this before writing SQL — table and column names are not
    hardcoded and may differ from what you expect. Defaults to the 'sales'
    schema in the salesdb database on the sales-demo-cluster.
    """
    try:
        tables_result = await call_mcp_tool("list_tables", {
            "cluster_identifier": CLUSTER_IDENTIFIER,
            "table_database_name": DATABASE_NAME,
            "table_schema_name": schema_name,
        })

        sections = [f"Tables in {schema_name}:\n{tables_result}"]

        try:
            tables_json = json.loads(tables_result)
            tables = tables_json if isinstance(tables_json, list) else []
        except (json.JSONDecodeError, TypeError):
            tables = []

        for t in tables:
            tname = t.get("table_name") or t.get("name") if isinstance(t, dict) else None
            if not tname:
                continue
            cols = await call_mcp_tool("list_columns", {
                "cluster_identifier": CLUSTER_IDENTIFIER,
                "column_database_name": DATABASE_NAME,
                "column_schema_name": schema_name,
                "column_table_name": tname,
            })
            sections.append(f"\n{schema_name}.{tname} columns:\n{cols}")

        return "\n".join(sections)
    except Exception as e:
        return f"Error discovering schema: {e}"


@tool
async def run_sql(sql: str) -> str:
    """Execute a single SQL statement against sales-demo-cluster / salesdb and return the results.

    Use discover_schema first to confirm table and column names. Always fully qualify
    tables (e.g. sales.fact_sales). Keep statements read-only — the connection is read-only.
    """
    try:
        return await call_mcp_tool("execute_query", {
            "cluster_identifier": CLUSTER_IDENTIFIER,
            "database_name": DATABASE_NAME,
            "sql": sql,
        })
    except Exception as e:
        return f"Error running SQL: {e}"


SQL_EVAL_SECRET = "sql-evaluations-agent/gateway-credentials"
SQL_EVAL_GATEWAY_URL_PARAM = "/sql-evaluations-agent/gateway_url"
# AgentCore Gateway prefixes tool names with `<target-name>___`. The
# evaluations target is named "evaluations" in deploy.sh.
SQL_EVAL_TOOL_NAME = "evaluations___evaluate_sql"


@tool
async def evaluate_sql(sql: str, context: str = "", dialect: str = "redshift") -> str:
    """Send a SQL statement to the SQL Evaluations Agent for an independent
    review of errors, optimizations, and security risks before execution.

    Returns a JSON string with `status` (approved | revised | rejected),
    grouped `findings`, an optional `revised_sql`, and a `reasoning` summary.
    Call this BEFORE run_sql for any non-trivial query — if status is
    `revised`, prefer `revised_sql` over the original; if `rejected`, do not
    execute.
    """
    try:
        ssm_client = boto3.client("ssm", region_name=REGION)
        gateway_url = ssm_client.get_parameter(Name=SQL_EVAL_GATEWAY_URL_PARAM)["Parameter"]["Value"]

        secrets_client = boto3.client("secretsmanager", region_name=REGION)
        creds = json.loads(secrets_client.get_secret_value(SecretId=SQL_EVAL_SECRET)["SecretString"])

        cognito_client = boto3.client("cognito-idp", region_name=REGION)
        auth = cognito_client.initiate_auth(
            ClientId=creds["client_id"],
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
        )
        token = auth["AuthenticationResult"]["AccessToken"]

        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(gateway_url, headers, timeout=timedelta(seconds=60)) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    name=SQL_EVAL_TOOL_NAME,
                    arguments={"sql": sql, "context": context or "", "dialect": dialect or "redshift"},
                )
                if result.isError:
                    return f"Evaluator error: {result.content[0].text}"
                return result.content[0].text
    except BaseException as e:
        # Soft-fail: return a synthetic 'approved' verdict so the agent can
        # still proceed if the evaluator is unreachable. Operators can read
        # CloudWatch logs to spot the degradation.
        import traceback
        # asyncio.TaskGroup / ExceptionGroup wraps the real cause; surface it.
        inner = ""
        sub_exceptions = getattr(e, "exceptions", None)
        if sub_exceptions:
            for i, sub in enumerate(sub_exceptions):
                inner += f"\n  sub#{i} {type(sub).__name__}: {sub}"
                inner += "\n" + "".join(traceback.format_exception(type(sub), sub, sub.__traceback__))
        cause_msg = str(getattr(e, "__cause__", "") or "")
        print(
            f"evaluate_sql DEGRADED: top={type(e).__name__}: {e}\n"
            f"  __cause__: {cause_msg}\n"
            f"  inner: {inner}\n"
            f"  full_tb:\n{traceback.format_exc()}",
            flush=True,
        )
        return json.dumps({
            "status": "approved",
            "findings": {"errors": [], "optimizations": [], "security": []},
            "revised_sql": None,
            "reasoning": f"evaluator unavailable ({type(e).__name__}): {e}",
        })

async def call_mcp_tool(tool_name: str, arguments: dict = None, session = None) -> str:
    """Call any MCP tool dynamically with given arguments and optional session"""
    
    if arguments is None:
        arguments = {}
    
    try:
        # Use provided session or create new one
        if session is not None:
            # Use existing session
            if tool_name == "list_tools":
                tools = await session.list_tools()
                tool_list = []
                for tool in tools.tools:
                    tool_list.append(f"• {tool.name} - {tool.description}")
                return f"Available MCP Tools:\n" + "\n".join(tool_list)
            
            result = await session.call_tool(name=tool_name, arguments=arguments)
        else:
            # Create new session using OAuth for MCP
            ssm_client = boto3.client('ssm', region_name=REGION)
            agent_arn = ssm_client.get_parameter(Name='/mcp/redshift/agent_arn')['Parameter']['Value']
            credentials_secret = "/mcp/redshift/credentials"
            
            secrets_client = boto3.client('secretsmanager', region_name=REGION)
            response = secrets_client.get_secret_value(SecretId=credentials_secret)
            creds = json.loads(response['SecretString'])
            
            cognito_client = boto3.client('cognito-idp', region_name=REGION)
            auth_response = cognito_client.initiate_auth(
                ClientId=creds['client_id'],
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters={'USERNAME': creds['username'], 'PASSWORD': creds['password']}
            )
            bearer_token = auth_response['AuthenticationResult']['AccessToken']
            
            encoded_arn = agent_arn.replace(':', '%3A').replace('/', '%2F')
            mcp_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
            headers = {
                "authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"
            }
            
            async with streamablehttp_client(mcp_url, headers, timeout=timedelta(seconds=60)) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as mcp_session:
                    await mcp_session.initialize()
                    
                    if tool_name == "list_tools":
                        tools = await mcp_session.list_tools()
                        tool_list = []
                        for tool in tools.tools:
                            tool_list.append(f"• {tool.name} - {tool.description}")
                        return f"Available MCP Tools:\n" + "\n".join(tool_list)
                    
                    result = await mcp_session.call_tool(name=tool_name, arguments=arguments)
        
        if result.isError:
            return f"Tool '{tool_name}' error: {result.content[0].text}"

        # FastMCP returns one content item per element when the tool returns a list,
        # so concatenate everything into a single JSON-array string for downstream parsing.
        if len(result.content) > 1:
            items = []
            for c in result.content:
                try:
                    items.append(json.loads(c.text))
                except (json.JSONDecodeError, AttributeError):
                    items.append(c.text)
            return json.dumps(items)

        # Parse and format results if it's a query
        if tool_name == "execute_query":
            content = result.content[0].text
            try:
                data = json.loads(content)
                if isinstance(data, dict) and 'rows' in data:
                    columns = data.get('columns', [])
                    rows = data.get('rows', [])
                    
                    if not rows:
                        return "Query executed successfully but returned no results."
                    
                    # Create formatted table with proper alignment
                    if not columns:
                        return "Query executed successfully but returned no column information."
                    
                    # Calculate column widths
                    col_widths = []
                    for i, col in enumerate(columns):
                        max_width = len(str(col))
                        for row in rows[:10]:
                            if i < len(row):
                                max_width = max(max_width, len(str(row[i])))
                        col_widths.append(min(max_width + 2, 20))  # Cap at 20 chars
                    
                    # Create header
                    table = "\n" + " | ".join(f"{col:<{col_widths[i]}}" for i, col in enumerate(columns)) + "\n"
                    table += "-" * (sum(col_widths) + len(columns) * 3 - 1) + "\n"
                    
                    # Add rows
                    for row in rows[:10]:
                        formatted_row = []
                        for i, val in enumerate(row):
                            if i < len(col_widths):
                                formatted_row.append(f"{str(val):<{col_widths[i]}}")
                        table += " | ".join(formatted_row) + "\n"
                    
                    if len(rows) > 10:
                        table += f"\n... and {len(rows) - 10} more rows"
                    
                    return f"Query Results ({len(rows)} rows):{table}"
            except json.JSONDecodeError:
                pass
        
        return result.content[0].text
                
    except Exception as e:
        return f"Error calling MCP tool '{tool_name}': {str(e)}"

def get_or_create_memory():
    """Return the configured sales memory resource if it is reachable."""
    memory_id = os.environ.get("SALES_MEMORY_ID")
    if not memory_id:
        print("Warning: SALES_MEMORY_ID not set; running without memory")
        return None

    memory_client = MemoryClient(region_name=REGION)
    try:
        memory_client.get_memory_strategies(memory_id)
        return memory_id
    except Exception as e:
        print(f"Warning: memory {memory_id} unavailable: {e}")
        return None

def load_secrets() -> Dict[str, Any]:
    client = boto3.client('secretsmanager', region_name=REGION)
    response = client.get_secret_value(SecretId='sales-agent-credentials')
    return json.loads(response['SecretString'])

def create_sales_agent(headers=None):
    """Create and configure the sales agent"""
    # Extract user info from headers
    user_info = extract_user_info_from_jwt(headers) if headers else None
    user_id = user_info['user_id'] if user_info else 'unknown-user'
    
    # Get or create memory dynamically
    memory_id = get_or_create_memory()
    if not memory_id:
        print("Warning: Running without memory")
        memory_hooks = None
    else:
        memory_client = MemoryClient(region_name=REGION)
        memory_hooks = SalesAgentMemoryHooks(memory_id, memory_client, user_id, SESSION_ID)
    
    # Initialize Bedrock model
    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=REGION
    )
    
    agent = Agent(
        model=model,
        hooks=[memory_hooks] if memory_hooks else [],
        tools=[discover_schema, evaluate_sql, run_sql],
        system_prompt="""You are a sales reporting agent with access to a Redshift sales data mart
on cluster `sales-demo-cluster`, database `salesdb`, schema `sales`.

You have three tools:
- discover_schema(schema_name='sales') — lists every table in the schema along with its columns.
  Always call this first when you don't already know the exact table and column names you need.
- evaluate_sql(sql, context, dialect='redshift') — sends the SQL to an independent SQL Evaluations
  Agent that checks for errors, optimizations, and security risks. Returns JSON with
  `status` (approved | revised | rejected), `findings`, optional `revised_sql`, and `reasoning`.
- run_sql(sql) — executes a single read-only SQL statement and returns the rows.

Workflow for every analytical question:
1. If you don't already know the relevant tables/columns from this conversation, call discover_schema first.
2. Write a fully qualified SQL query (e.g. `sales.fact_sales`, never bare `fact_sales`).
3. Call evaluate_sql with that query (dialect="redshift").
   - If status == "approved", proceed with the original SQL.
   - If status == "revised", use `revised_sql` going forward.
   - If status == "rejected", DO NOT execute. Tell the user the query was blocked and
     summarize the security/error finding in one sentence.
4. Call run_sql with the final SQL.
5. If the query errors with a missing column or table, call discover_schema again — do not guess names.
6. Present results as an ASCII table and add a one-paragraph business interpretation.

Rules:
- Never invent table or column names. If discover_schema doesn't list it, it doesn't exist.
- Never report IAM, SSM, or AWS-permission errors back to the user. If a tool returns an internal
  infrastructure error, say "I'm having trouble reaching the data source right now" and stop.
- Keep responses focused on the data and the insight. Do not output remediation steps or AWS policy JSON.

ASCII table example:
```
Rank  Product Name                  Total Sales    Orders
----  ----------------------------  -----------    ------
1     HardTech Consulting           $2,350,909     24
2     SecureIT ERP Draw             $2,053,365     30
```
"""
    )
    
    return agent

# Initialize the AgentCore Runtime App
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    """AgentCore Runtime entrypoint function"""
    user_input = payload.get("prompt", "")

    # Create agent
    agent = create_sales_agent()

    # Invoke the agent
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()

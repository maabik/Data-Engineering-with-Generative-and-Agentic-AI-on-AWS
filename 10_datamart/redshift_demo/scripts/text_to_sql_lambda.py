import json
import os
import re
import time

import boto3
from botocore.exceptions import ClientError

bedrock = boto3.client('bedrock-runtime')
bedrock_agent = boto3.client('bedrock-agent-runtime')
dynamodb = boto3.resource('dynamodb')
secrets_manager = boto3.client('secretsmanager')
redshift_data = boto3.client('redshift-data')

PROMPT_TEMPLATE_TABLE = os.environ.get('PROMPT_TEMPLATE_TABLE', 'TextToSqlPromptTemplates')
PROMPT_TEMPLATE_ID = os.environ.get('PROMPT_TEMPLATE_ID', 'redshift-sales-schema')
SECRETS_MANAGER_REDSHIFT_SECRET = os.environ.get('REDSHIFT_SECRET_ARN')
TEXT_TO_SQL_AGENT_ID = os.environ.get('TEXT_TO_SQL_AGENT_ID')
TEXT_TO_SQL_AGENT_ALIAS_ID = os.environ.get('TEXT_TO_SQL_AGENT_ALIAS_ID')

# Cross-region inference profile for Sonnet 4.6 (us-* regions). Override via env var
# if running in a non-US region or to pin a specific model version.
FALLBACK_MODEL_ID = os.environ.get(
    'FALLBACK_MODEL_ID',
    'us.anthropic.claude-sonnet-4-6',
)
QUERY_POLL_MAX_ATTEMPTS = int(os.environ.get('QUERY_POLL_MAX_ATTEMPTS', '20'))
MAX_USER_QUERY_LEN = int(os.environ.get('MAX_USER_QUERY_LEN', '2000'))

# Read-only allow-list. Any LLM-generated SQL whose first non-comment token
# is not in this set is rejected before reaching Redshift, regardless of
# whether the user prompt tried to talk the model into a write. This is the
# mitigation for prompt-injection -> SQL-injection: the model can be tricked
# into emitting DROP/COPY/UNLOAD; this guard blocks the execution.
ALLOWED_LEADING_KEYWORDS = {"SELECT", "WITH"}

# Tokens that must not appear anywhere in the final SQL even if it starts
# with SELECT (e.g. ";DROP ...", "SELECT ... INTO", "SELECT ... UNLOAD").
# Matched as whole words, case-insensitive.
DISALLOWED_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "UNLOAD", "CALL", "EXECUTE", "MERGE",
    "VACUUM", "ANALYZE", "COMMENT", "SET", "RESET", "BEGIN", "COMMIT",
    "ROLLBACK", "INTO",
)
_DISALLOWED_RE = re.compile(
    r"\b(" + "|".join(DISALLOWED_KEYWORDS) + r")\b", re.IGNORECASE
)
_LEADING_COMMENT_RE = re.compile(r"^\s*(--[^\n]*\n|/\*.*?\*/)", re.DOTALL)


def lambda_handler(event, context):
    try:
        if 'body' not in event:
            return format_response(400, {"error": "Missing request body"})

        body = json.loads(event['body'])
        user_query = body.get('query')
        if not user_query:
            return format_response(400, {"error": "Missing 'query' parameter"})
        if not isinstance(user_query, str) or len(user_query) > MAX_USER_QUERY_LEN:
            return format_response(400, {"error": f"'query' must be a string under {MAX_USER_QUERY_LEN} characters"})

        session_id = body.get('sessionId') or f"session-{int(time.time() * 1000)}"

        try:
            sql_query = generate_sql_with_agent(user_query, session_id)
            if not sql_query:
                raise RuntimeError("Agent returned empty SQL")
            print(f"SQL via TextToSQLAgent: {sql_query}")
        except Exception as agent_error:
            print(f"TextToSQLAgent failed ({agent_error}); falling back to Bedrock direct invoke.")
            prompt_template = get_prompt_template(PROMPT_TEMPLATE_ID)
            if not prompt_template:
                return format_response(500, {"error": "Failed to retrieve prompt template and agent failed"})
            sql_query = generate_sql_with_bedrock(user_query, prompt_template)
            if not sql_query:
                return format_response(500, {"error": "Both agent and direct Bedrock failed to generate SQL"})

        try:
            sql_query = enforce_readonly_sql(sql_query)
        except ValueError as guard_error:
            return format_response(400, {
                "error": "Generated SQL was not a read-only query and was blocked",
                "reason": str(guard_error),
                "sql": sql_query,
            })

        redshift_creds = get_redshift_credentials()
        if not redshift_creds:
            return format_response(500, {"error": "Failed to retrieve Redshift credentials"})

        query_result = execute_redshift_query(sql_query, redshift_creds)

        return format_response(200, {
            "query": user_query,
            "sql": sql_query,
            "results": query_result,
        })

    except Exception as e:
        print(f"Error: {e}")
        return format_response(500, {"error": str(e)})


def generate_sql_with_agent(user_query, session_id):
    """Generate SQL via the configured Bedrock TextToSQL Agent."""
    if not TEXT_TO_SQL_AGENT_ID or not TEXT_TO_SQL_AGENT_ALIAS_ID:
        raise RuntimeError("TEXT_TO_SQL_AGENT_ID / TEXT_TO_SQL_AGENT_ALIAS_ID not configured")

    response = bedrock_agent.invoke_agent(
        agentId=TEXT_TO_SQL_AGENT_ID,
        agentAliasId=TEXT_TO_SQL_AGENT_ALIAS_ID,
        sessionId=session_id,
        inputText=user_query,
    )

    sql_query = ""
    for event in response['completion']:
        chunk = event.get('chunk') or {}
        if 'bytes' in chunk:
            sql_query += chunk['bytes'].decode('utf-8')

    return strip_sql_fences(sql_query.strip())


def get_prompt_template(template_id):
    """Retrieve prompt template from DynamoDB (fallback)."""
    try:
        table = dynamodb.Table(PROMPT_TEMPLATE_TABLE)
        response = table.get_item(Key={'id': template_id})
        if 'Item' in response:
            return response['Item'].get('template')
        print(f"Prompt template with ID {template_id} not found")
        return None
    except ClientError as e:
        print(f"Error retrieving prompt template: {e}")
        return None


def generate_sql_with_bedrock(user_query, prompt_template):
    """Fallback: call Claude Sonnet 4.6 directly via Bedrock Messages API."""
    try:
        prompt_content = prompt_template.replace('{user_query}', user_query)

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2000,
            "temperature": 0.0,
            "system": (
                "You are a Redshift SQL expert. Output only the SQL query for the "
                "user's request, no preamble, no explanation, no markdown fences."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Schema and guidelines:\n{prompt_content}\n\n"
                                f"User request: {user_query}\n\n"
                                "Return only the SQL query."
                            ),
                        }
                    ],
                }
            ],
        }

        response = bedrock.invoke_model(
            modelId=FALLBACK_MODEL_ID,
            body=json.dumps(request_body),
        )
        response_body = json.loads(response['body'].read())
        # Messages API: content is a list of blocks; we want the text block(s).
        content_blocks = response_body.get('content', [])
        text = ''.join(block.get('text', '') for block in content_blocks if block.get('type') == 'text')
        return strip_sql_fences(text.strip())

    except ClientError as e:
        print(f"Error generating SQL with Bedrock: {e}")
        return None


def strip_sql_fences(text):
    """Remove ```sql ... ``` or ``` ... ``` fences if the model wrapped them."""
    if "```sql" in text:
        return text.split("```sql", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def enforce_readonly_sql(sql_query):
    """Refuse to execute anything that is not a single read-only statement.

    Defense against prompt-injection: even if the LLM is talked into emitting
    write/DDL SQL, this guard is what actually faces Redshift. Bypassing it
    requires a model bug AND a regex bypass on a denylist of SQL keywords.
    """
    if not sql_query or not sql_query.strip():
        raise ValueError("empty SQL")

    cleaned = sql_query.strip().rstrip(";").strip()
    while True:
        m = _LEADING_COMMENT_RE.match(cleaned)
        if not m:
            break
        cleaned = cleaned[m.end():].lstrip()

    if ";" in cleaned:
        raise ValueError("multiple statements are not allowed")

    leading = cleaned.split(None, 1)[0].upper() if cleaned.split() else ""
    if leading not in ALLOWED_LEADING_KEYWORDS:
        raise ValueError(f"only {sorted(ALLOWED_LEADING_KEYWORDS)} statements are allowed; got '{leading}'")

    forbidden = _DISALLOWED_RE.search(cleaned)
    if forbidden:
        raise ValueError(f"forbidden keyword '{forbidden.group(1).upper()}' in query")

    return cleaned


def get_redshift_credentials():
    """Retrieve Redshift credentials from Secrets Manager."""
    if not SECRETS_MANAGER_REDSHIFT_SECRET:
        print("REDSHIFT_SECRET_ARN env var not set")
        return None
    try:
        response = secrets_manager.get_secret_value(SecretId=SECRETS_MANAGER_REDSHIFT_SECRET)
        return json.loads(response['SecretString'])
    except ClientError as e:
        print(f"Error retrieving Redshift credentials: {e}")
        return None


def execute_redshift_query(sql_query, credentials):
    """Execute SQL query using the Redshift Data API."""
    try:
        cluster_identifier = credentials.get('cluster_identifier')
        database = credentials.get('database')
        db_user = credentials.get('username')

        response = redshift_data.execute_statement(
            ClusterIdentifier=cluster_identifier,
            Database=database,
            DbUser=db_user,
            Sql=sql_query,
        )
        query_id = response['Id']

        attempts = 0
        while attempts < QUERY_POLL_MAX_ATTEMPTS:
            status_response = redshift_data.describe_statement(Id=query_id)
            status = status_response.get('Status')
            if status == 'FINISHED':
                break
            if status in {'FAILED', 'ABORTED'}:
                raise Exception(f"Query execution failed: {status_response.get('Error', 'Unknown error')}")
            time.sleep(1)
            attempts += 1

        if attempts >= QUERY_POLL_MAX_ATTEMPTS:
            raise Exception("Query execution timed out")

        result = redshift_data.get_statement_result(Id=query_id)
        columns = [col['name'] for col in result['ColumnMetadata']]
        rows = []
        for record in result['Records']:
            row = {}
            for i, value in enumerate(record):
                field_type = next(iter(value), None) if value else None
                row[columns[i]] = value.get(field_type) if field_type else None
            rows.append(row)
        return {"columns": columns, "rows": rows}

    except ClientError as e:
        print(f"Error executing Redshift query: {e}")
        return {"error": str(e)}


def format_response(status_code, body):
    """Format API Gateway response with permissive CORS headers."""
    return {
        'statusCode': status_code,
        'body': json.dumps(body),
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET',
            'Access-Control-Allow-Headers': 'Content-Type',
        },
    }

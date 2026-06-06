# Sales Reporting Agent Specification

## Overview
A natural language to SQL sales reporting agent deployed on AWS Bedrock AgentCore runtime that processes sales queries and returns formatted results from a Redshift data warehouse.

## Architecture Components

### 1. Authentication & Authorization
- **Cognito JWT Authorizer**: User authentication via AWS Cognito User Pool
- **Secrets Manager**: Secure storage of Redshift credentials and Cognito client config
- **IAM Roles**: Execution role for AgentCore runtime with necessary permissions

### 2. Core Agent Functionality
- **Natural Language Processing**: Accept sales questions in plain English
- **Text-to-SQL Conversion**: Use Amazon Bedrock Claude Sonnet 4.0 for SQL generation
- **Schema Awareness**: Full knowledge of sales data warehouse schema
- **Query Execution**: Dynamic SQL execution via Redshift MCP server
- **Response Formatting**: Structured, human-readable query results

### 3. Data Layer
- **Redshift Data Warehouse**: Sales schema with fact and dimension tables
- **MCP Server**: Redshift Model Context Protocol server for query execution
- **Schema Integration**: Complete sales schema with relationships and views

## Technical Specifications

### Agent Runtime
- **Platform**: AWS Bedrock AgentCore
- **Runtime Environment**: Linux/ARM64 container
- **Transport**: Streamable HTTP
- **Authentication**: Cognito JWT Bearer tokens

### AI Model
- **Model**: Amazon Bedrock Claude Sonnet 4.0
- **Purpose**: Natural language to SQL conversion
- **Context**: Full sales schema and business rules

### Data Warehouse Schema
- **Schema**: `sales`
- **Architecture**: Star schema design
- **Fact Table**: `fact_sales` (main transaction table)
- **Dimensions**: 9 dimension tables (time, account, product, sales_rep, territory, district, region, zipcode, dma)
- **Views**: Pre-aggregated views for common queries

## Security Model

### Authentication Flow
1. User authenticates with Cognito User Pool
2. Receives JWT access token
3. Token validated by AgentCore JWT authorizer
4. Agent retrieves Redshift credentials from Secrets Manager
5. Secure connection to Redshift via MCP server

### Permissions
- **AgentCore Execution Role**: Bedrock model access, Secrets Manager read
- **Redshift User**: Read-only access to sales schema
- **Network Security**: VPC-based if required, otherwise public with encryption

## API Interface

### Input Format
```json
{
  "query": "What were the top 5 products by sales in Q4 2023?",
  "format": "table|json|summary"
}
```

### Output Format
```json
{
  "sql_generated": "SELECT ...",
  "results": {
    "columns": ["product_name", "total_sales"],
    "rows": [["Product A", 150000], ["Product B", 120000]],
    "row_count": 5,
    "execution_time_ms": 245
  },
  "summary": "Top 5 products by sales in Q4 2023"
}
```

## Deployment Configuration

### Environment Variables
- `COGNITO_USER_POOL_ID`: Cognito User Pool identifier
- `COGNITO_CLIENT_ID`: Cognito client application ID
- `SECRETS_MANAGER_SECRET_ID`: Secret containing Redshift credentials
- `BEDROCK_MODEL_ID`: Claude Sonnet 4.0 model identifier
- `REDSHIFT_CLUSTER_ID`: Target Redshift cluster
- `REDSHIFT_DATABASE`: Target database name

### Secrets Manager Structure
```json
{
  "redshift": {
    "host": "cluster-endpoint",
    "port": 5439,
    "database": "salesdb",
    "username": "sales_reader",
    "password": "secure_password"
  },
  "cognito": {
    "user_pool_id": "us-east-1_xxxxxxx",
    "client_id": "xxxxxxxxxxxxxxxxx",
    "region": "us-east-1"
  }
}
```

## Query Capabilities

### Supported Query Types
1. **Sales Performance**: Revenue, profit, growth metrics
2. **Time-based Analysis**: Daily, monthly, quarterly, yearly trends
3. **Geographic Analysis**: Regional, district, territory performance
4. **Product Analysis**: Category, brand, individual product metrics
5. **Sales Rep Performance**: Individual and team metrics
6. **Customer Analysis**: Account-based reporting

### Example Queries
- "Show me monthly sales for 2023 by region"
- "What are the top 10 products by profit margin?"
- "Which sales reps exceeded their targets last quarter?"
- "Compare Q4 2023 vs Q4 2022 sales performance"

## Error Handling

### SQL Generation Errors
- Invalid query interpretation
- Schema validation failures
- Unsupported query patterns

### Execution Errors
- Database connectivity issues
- Permission denied errors
- Query timeout handling

### Response Formatting
- Graceful error messages
- Suggested query alternatives
- Help text for common patterns

## Monitoring & Observability

### Metrics
- Query response times
- SQL generation accuracy
- Error rates by category
- User authentication success rates

### Logging
- All user queries (anonymized)
- Generated SQL statements
- Execution times and results
- Error conditions and resolutions

## Compliance & Governance

### Data Privacy
- No PII in logs or responses
- Secure credential management
- Encrypted data transmission

### Access Control
- Role-based access to different data views
- Query result filtering based on user permissions
- Audit trail of all data access

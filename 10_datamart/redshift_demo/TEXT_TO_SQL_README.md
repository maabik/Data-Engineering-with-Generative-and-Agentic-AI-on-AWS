# Text-to-SQL API for Redshift Sales Data Warehouse

This extension adds a natural language to SQL conversion capability to the Redshift Sales Data Warehouse demo. Users can ask questions about their sales data in plain English, and the system will generate and execute optimized SQL queries against the Redshift cluster.

## Architecture

The solution uses the following AWS services:

- **Amazon Bedrock**: Powers the LLM-based text-to-SQL conversion using Claude 4 Sonnet
- **Amazon Bedrock Agents (Strands)**: Specialized agents for SQL generation and validation
- **AWS Lambda**: Processes API requests and orchestrates the workflow
- **Amazon API Gateway**: Provides REST API endpoints
- **Amazon DynamoDB**: Stores prompt templates with schema information
- **AWS Secrets Manager**: Securely stores Redshift credentials
- **Amazon Redshift Data API**: Executes SQL queries against the Redshift cluster

## Deployment

### Prerequisites

- Existing Redshift Sales Data Warehouse deployment
- AWS CLI configured with appropriate permissions
- Python 3.7+ with boto3 installed

### Deployment Steps

1. **Deploy the CloudFormation stack**:

```bash
# Navigate to the scripts directory
cd scripts

# Deploy the text-to-SQL API
python deploy_text_to_sql.py
```

2. **Update the frontend configuration**:

After deployment, the script will output the API Gateway endpoint URL. Update the `API_ENDPOINT` variable in `frontend/index.html` with this URL.

3. **Host the frontend**:

You can host the frontend on any web server or use Amazon S3 static website hosting:

```bash
# Create an S3 bucket for the frontend
aws s3 mb s3://redshift-sales-text-to-sql-frontend

# Upload the frontend files
aws s3 cp frontend/ s3://redshift-sales-text-to-sql-frontend/ --recursive

# Configure the bucket for static website hosting
aws s3 website s3://redshift-sales-text-to-sql-frontend/ --index-document index.html
```

## Usage

1. Open the frontend application in your web browser
2. Enter a natural language question about your sales data
3. Click "Generate Report" to convert your question to SQL and execute it
4. View the generated SQL query and the query results

### Example Questions

- "Show me the top 10 products by sales amount"
- "Analyze sales by customer segment and industry"
- "Compare sales performance by region for the last 3 months"
- "Show me the sales representatives with the highest profit margin"
- "What is the average order value by customer segment?"
- "Show me the monthly sales trend for the past year"

## Customization

### Modifying the Prompt Template

The prompt template contains the schema information and guidelines for SQL generation. You can modify it in the DynamoDB table:

1. Open the AWS Console and navigate to DynamoDB
2. Find the `TextToSqlPromptTemplates` table
3. Edit the item with ID `redshift-sales-schema`

### Adding New Agents

You can create additional specialized agents for different types of analysis:

1. Create a new Bedrock Agent with appropriate instructions
2. Update the CloudFormation template to include the new agent
3. Modify the Lambda function to use the new agent for specific query types

## Security Considerations

- The Lambda function uses IAM roles with least privilege
- Redshift credentials are stored in AWS Secrets Manager
- SQL validation prevents potentially harmful queries
- API Gateway can be configured with authentication

## Troubleshooting

### Common Issues

1. **API returns 500 error**:
   - Check CloudWatch Logs for the Lambda function
   - Verify Redshift credentials in Secrets Manager
   - Ensure the Lambda has proper permissions

2. **SQL generation fails**:
   - Check the prompt template in DynamoDB
   - Verify the Bedrock Agent is properly configured

3. **Query execution fails**:
   - Check that the Redshift cluster is running
   - Verify the schema and table names in the prompt template

## Cost Optimization

- The Lambda function uses minimal memory (256MB)
- Bedrock Agents are only invoked when needed
- DynamoDB uses on-demand capacity for cost efficiency
- Consider implementing caching for frequently asked questions

## Future Enhancements

- Add user authentication and personalized views
- Implement query history and favorites
- Add data visualization capabilities
- Support more complex analytical questions
- Implement feedback mechanism to improve SQL generation

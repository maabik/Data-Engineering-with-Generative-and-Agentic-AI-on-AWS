#!/usr/bin/env python3
"""
Deploy Text-to-SQL API for Redshift Sales Data Warehouse
"""

import argparse
import boto3
import json
import os
import time
import zipfile
import io
from botocore.exceptions import ClientError

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Deploy Text-to-SQL API for Redshift Sales Data Warehouse')
    parser.add_argument('--region', default=os.environ.get('AWS_REGION', 'us-east-1'), help='AWS region')
    parser.add_argument('--stack-name', default='text-to-sql-api', help='CloudFormation stack name')
    parser.add_argument('--redshift-cluster', default='sales-demo-cluster', help='Redshift cluster identifier')
    parser.add_argument('--redshift-db', default='salesdb', help='Redshift database name')
    parser.add_argument('--redshift-user', default='admin', help='Redshift username')
    parser.add_argument(
        '--bedrock-agent-id',
        default=os.environ.get('BEDROCK_AGENT_ID'),
        required='BEDROCK_AGENT_ID' not in os.environ,
        help='Bedrock TextToSQL Agent ID (or set BEDROCK_AGENT_ID env var)',
    )
    parser.add_argument(
        '--bedrock-agent-alias-id',
        default=os.environ.get('BEDROCK_AGENT_ALIAS_ID'),
        required='BEDROCK_AGENT_ALIAS_ID' not in os.environ,
        help='Bedrock TextToSQL Agent Alias ID (or set BEDROCK_AGENT_ALIAS_ID env var)',
    )
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    return parser.parse_args()


def get_redshift_password():
    """Read Redshift master password from MASTER_PASSWORD env var. Required."""
    password = os.environ.get('MASTER_PASSWORD')
    if not password:
        raise SystemExit(
            "MASTER_PASSWORD env var is required. Set it to the Redshift master "
            "password before running this script."
        )
    return password

def create_lambda_zip():
    """Create ZIP file for Lambda function"""
    print("Creating Lambda deployment package...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    lambda_file = os.path.join(script_dir, 'text_to_sql_lambda.py')
    
    # Create in-memory zip file
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.write(lambda_file, os.path.basename(lambda_file))
    
    zip_buffer.seek(0)
    return zip_buffer.read()

def create_prompt_template():
    """Create SQL schema prompt template"""
    return """
    Generate a SQL query for Amazon Redshift based on the following requirements:

    {user_query}

    Database Schema:
    - Fact table: fact_sales (contains: account_key, product_key, sales_rep_key, time_key, territory_key, sales_amount, profit_amount, quantity)
    - Dimension tables:
      * dim_account (account_key, segment, industry, revenue)
      * dim_product (product_key, category, brand, price)
      * dim_sales_rep (sales_rep_key, name, team, hire_date)
      * dim_time (time_key, date, month, quarter, year, fiscal_period)
      * dim_sales_territory (territory_key, district_key, region_key)
      * dim_district (district_key, district_name)
      * dim_region (region_key, region_name)
      * dim_zipcode (zipcode_key, latitude, longitude)
      * dim_dma (dma_key, dma_name)

    Guidelines:
    1. Use the 'sales' schema for all tables
    2. Include appropriate JOINs between fact and dimension tables
    3. Use descriptive column aliases for calculated fields
    4. Format the query with proper indentation and comments
    5. Consider performance optimization for Redshift
    6. Include appropriate filters, grouping, and sorting
    7. Pay attention to _key vs _id naming conventions (use _key for dimension keys)
    
    Output only the SQL query without any explanations.
    """

def deploy_cloudformation(args, lambda_zip, redshift_password):
    """Deploy CloudFormation stack"""
    print(f"Deploying CloudFormation stack {args.stack_name}...")

    cfn = boto3.client('cloudformation', region_name=args.region)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'text_to_sql_cfn.yaml')
    with open(template_path, 'r') as f:
        template_body = f.read()

    s3 = boto3.client('s3', region_name=args.region)
    bucket_name = f"text-to-sql-lambda-{int(time.time())}"
    if args.region == 'us-east-1':
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={'LocationConstraint': args.region},
        )

    s3_key = 'text_to_sql_lambda.zip'
    s3.put_object(Bucket=bucket_name, Key=s3_key, Body=lambda_zip)

    try:
        response = cfn.create_stack(
            StackName=args.stack_name,
            TemplateBody=template_body,
            Parameters=[
                {'ParameterKey': 'RedshiftClusterIdentifier', 'ParameterValue': args.redshift_cluster},
                {'ParameterKey': 'RedshiftDatabase', 'ParameterValue': args.redshift_db},
                {'ParameterKey': 'RedshiftUsername', 'ParameterValue': args.redshift_user},
                {'ParameterKey': 'RedshiftPassword', 'ParameterValue': redshift_password},
                {'ParameterKey': 'PromptTemplateId', 'ParameterValue': 'redshift-sales-schema'},
                {'ParameterKey': 'TextToSqlAgent', 'ParameterValue': args.bedrock_agent_id},
                {'ParameterKey': 'TextToSqlAgentAlias', 'ParameterValue': args.bedrock_agent_alias_id},
            ],
            Capabilities=['CAPABILITY_IAM'],
            OnFailure='ROLLBACK',
        )

        stack_id = response['StackId']
        print(f"Stack creation initiated. Stack ID: {stack_id}")

        print("Waiting for stack creation to complete...")
        waiter = cfn.get_waiter('stack_create_complete')
        waiter.wait(StackName=args.stack_name)

        response = cfn.describe_stacks(StackName=args.stack_name)
        outputs = {output['OutputKey']: output['OutputValue'] for output in response['Stacks'][0]['Outputs']}

        print("\nStack creation completed successfully!")
        print(f"API Endpoint: {outputs['ApiEndpoint']}")

        lambda_client = boto3.client('lambda', region_name=args.region)
        lambda_client.update_function_code(
            FunctionName=outputs['LambdaFunctionName'],
            S3Bucket=bucket_name,
            S3Key=s3_key,
        )

        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(outputs['PromptTemplateTableName'])
        table.put_item(Item={'id': 'redshift-sales-schema', 'template': create_prompt_template()})

        print("Prompt template added to DynamoDB")
        print("\nText-to-SQL API deployment complete!")
        return True

    except ClientError as e:
        print(f"Error deploying CloudFormation stack: {e}")
        return False


def main():
    """Main function"""
    args = parse_args()
    redshift_password = get_redshift_password()
    lambda_zip = create_lambda_zip()
    deploy_cloudformation(args, lambda_zip, redshift_password)

if __name__ == '__main__':
    main()

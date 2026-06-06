#!/usr/bin/env python3
"""
Comprehensive deployment script for Redshift Sales Data Warehouse with Text-to-SQL API
"""

import argparse
import os
import subprocess
import sys
import time

# Make local scripts importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import generate_redshift_password


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Deploy Redshift Sales Data Warehouse with Text-to-SQL API')
    parser.add_argument('--config-file', default='config.yaml', help='Configuration file path')
    parser.add_argument('--skip-generate-data', action='store_true', help='Skip data generation')
    parser.add_argument('--skip-cluster-creation', action='store_true', help='Skip Redshift cluster creation')
    parser.add_argument('--skip-text-to-sql', action='store_true', help='Skip Text-to-SQL API deployment')
    parser.add_argument('--bucket-name', help='Existing S3 bucket name for data staging')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument(
        '--bedrock-agent-id',
        default=os.environ.get('BEDROCK_AGENT_ID'),
        help='Bedrock TextToSQL Agent ID (or set BEDROCK_AGENT_ID env var)',
    )
    parser.add_argument(
        '--bedrock-agent-alias-id',
        default=os.environ.get('BEDROCK_AGENT_ALIAS_ID'),
        help='Bedrock TextToSQL Agent Alias ID (or set BEDROCK_AGENT_ALIAS_ID env var)',
    )
    return parser.parse_args()


def resolve_password():
    """Pull master password from env, or generate one and warn the user."""
    password = os.environ.get('MASTER_PASSWORD')
    if password:
        return password, False
    password = generate_redshift_password()
    print("MASTER_PASSWORD env var not set; generated a random password.")
    print("It will be persisted in AWS Secrets Manager (redshift-text-to-sql-credentials).")
    return password, True

def run_command(command, description, verbose=False):
    """Run a shell command and handle errors"""
    print(f"\n=== {description} ===")
    
    if verbose:
        print(f"Running: {' '.join(command)}")
    
    try:
        if verbose:
            result = subprocess.run(command, check=True)
        else:
            result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        if not verbose and e.stderr:
            print(f"Error details: {e.stderr.decode('utf-8')}")
        return False

def deploy_data_warehouse(args, password):
    """Deploy the Redshift data warehouse"""
    print("\n=== Deploying Redshift Sales Data Warehouse ===")

    command = [sys.executable, 'scripts/deploy.py']

    if args.config_file != 'config.yaml':
        command.extend(['--config-file', args.config_file])
    if args.skip_generate_data:
        command.append('--skip-generate-data')
    if args.skip_cluster_creation:
        command.append('--skip-cluster-creation')
    if args.bucket_name:
        command.extend(['--bucket-name', args.bucket_name])

    env = os.environ.copy()
    env['MASTER_PASSWORD'] = password

    try:
        if args.verbose:
            subprocess.run(command, check=True, env=env)
        else:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error deploying data warehouse: {e}")
        if not args.verbose and e.stderr:
            print(f"Error details: {e.stderr.decode('utf-8')}")
        return False


def deploy_text_to_sql(args, password):
    """Deploy the Text-to-SQL API"""
    print("\n=== Deploying Text-to-SQL API ===")

    if not args.bedrock_agent_id or not args.bedrock_agent_alias_id:
        print("Error: --bedrock-agent-id and --bedrock-agent-alias-id are required.")
        print("Create a Bedrock Agent in the console first, then provide the IDs.")
        return False

    command = [
        sys.executable,
        'scripts/deploy_text_to_sql.py',
        '--bedrock-agent-id', args.bedrock_agent_id,
        '--bedrock-agent-alias-id', args.bedrock_agent_alias_id,
    ]
    if args.verbose:
        command.append('--verbose')

    env = os.environ.copy()
    env['MASTER_PASSWORD'] = password

    print(f"\n=== Deploying Text-to-SQL API ===")
    try:
        subprocess.run(command, check=True, env=env)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error deploying Text-to-SQL API: {e}")
        return False

def main():
    """Main function"""
    args = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(os.path.dirname(script_dir))  # project root

    password, generated = resolve_password()

    if not deploy_data_warehouse(args, password):
        print("Failed to deploy data warehouse. Exiting.")
        sys.exit(1)

    if not args.skip_text_to_sql:
        if not deploy_text_to_sql(args, password):
            print("Failed to deploy Text-to-SQL API.")
            print("Data warehouse deployment was successful, but Text-to-SQL API deployment failed.")
            sys.exit(1)

    if generated:
        print("\nMaster password retrieval:")
        print("  aws secretsmanager get-secret-value --secret-id redshift-text-to-sql-credentials --query SecretString --output text")
    
    print("\n=== Deployment Complete ===")
    print("Your Redshift Sales Data Warehouse with Text-to-SQL API has been successfully deployed!")
    
    # Wait a moment to ensure all resources are fully available
    time.sleep(5)
    
    # Get cluster details
    try:
        result = subprocess.run(
            ['aws', 'redshift', 'describe-clusters', '--cluster-identifier', 'sales-demo-cluster', '--query', 'Clusters[0].Endpoint'],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        endpoint_info = result.stdout.decode('utf-8').strip()
        print(f"\nRedshift Endpoint: {endpoint_info}")
    except subprocess.CalledProcessError:
        print("\nCouldn't retrieve Redshift endpoint information. Please check the AWS Console.")
    
    # Get API Gateway endpoint
    if not args.skip_text_to_sql:
        try:
            result = subprocess.run(
                ['aws', 'cloudformation', 'describe-stacks', '--stack-name', 'text-to-sql-api', '--query', 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue', '--output', 'text'],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            api_endpoint = result.stdout.decode('utf-8').strip()
            print(f"Text-to-SQL API Endpoint: {api_endpoint}")
            print("\nRemember to update the API_ENDPOINT variable in frontend/index.html with this URL.")
        except subprocess.CalledProcessError:
            print("\nCouldn't retrieve API Gateway endpoint. Please check the AWS Console.")
    
    print("\nNext steps:")
    print("1. Connect to your Redshift cluster using the endpoint above")
    print("2. Run sample queries from sample_queries.sql")
    if not args.skip_text_to_sql:
        print("3. Update the API_ENDPOINT in frontend/index.html")
        print("4. Host the frontend application")
        print("5. Try asking questions about your sales data!")

if __name__ == '__main__':
    main()

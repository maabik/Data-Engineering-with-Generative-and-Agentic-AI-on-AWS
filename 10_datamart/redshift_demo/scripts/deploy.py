#!/usr/bin/env python3
import os
import sys
import time
import boto3
import argparse
import subprocess
from pathlib import Path
from config import config

def run_script(script_name, args=None):
    """Run a Python script with optional arguments"""
    cmd = [sys.executable, script_name]
    if args:
        cmd.extend(args)
    
    print(f"\n{'='*80}")
    print(f"Running {script_name}")
    print(f"{'='*80}\n")
    
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"Error running {script_name}")
        sys.exit(1)
    return True

def create_iam_role(bucket_name):
    """Create IAM role for Redshift, scoped to the staging bucket."""
    try:
        import json

        region = config.get_aws_region()
        role_name = config.get_iam_role_name()

        iam = boto3.client('iam', region_name=region)

        # Check if role already exists
        try:
            iam.get_role(RoleName=role_name)
            print(f"IAM role {role_name} already exists")
            # Still ensure it has access to the (possibly new) staging bucket
            _attach_bucket_policy(iam, role_name, bucket_name)
            return True
        except iam.exceptions.NoSuchEntityException:
            pass

        trust_policy_file = Path(__file__).parent / 'redshift-role-trust-policy.json'
        with open(trust_policy_file, 'r') as f:
            trust_policy = f.read()

        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description='Role for Redshift Sales Demo'
        )

        _attach_bucket_policy(iam, role_name, bucket_name)
        print(f"Created IAM role {role_name} scoped to s3://{bucket_name}")

        print("Waiting for role to propagate...")
        time.sleep(10)
        return True
    except Exception as e:
        print(f"Error creating IAM role: {e}")
        return False


def _attach_bucket_policy(iam, role_name, bucket_name):
    """Attach an inline policy granting read-only access to a single bucket."""
    import json

    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": f"arn:aws:s3:::{bucket_name}",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"RedshiftStagingBucketAccess-{bucket_name}",
        PolicyDocument=json.dumps(policy_doc),
    )

def create_s3_bucket(bucket_name):
    """Create S3 bucket for data staging"""
    try:
        region = config.get_aws_region()
        s3_client = boto3.client('s3', region_name=region)
        
        # Check if bucket exists
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            print(f"S3 bucket {bucket_name} already exists")
            return True
        except:
            pass
        
        # Create bucket
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region}
            )
        
        print(f"Created S3 bucket {bucket_name}")
        return True
    except Exception as e:
        print(f"Error creating S3 bucket: {e}")
        return False

def upload_data_to_s3(bucket_name):
    """Upload data files to S3"""
    try:
        region = config.get_aws_region()
        data_dir = Path(__file__).parent.parent / 'data'
        
        print(f"Uploading data from {data_dir} to S3 bucket {bucket_name}")
        
        result = subprocess.run([
            'aws', 's3', 'cp', str(data_dir), 
            f's3://{bucket_name}/data/', 
            '--recursive', 
            '--region', region
        ])
        
        if result.returncode != 0:
            print("Error uploading data to S3")
            return False
        
        print("Data uploaded to S3 successfully")
        return True
    except Exception as e:
        print(f"Error uploading data to S3: {e}")
        return False

def print_connection_details():
    """Print connection details for the cluster"""
    try:
        from cluster_utils import get_cluster_endpoint
        
        connection_details = get_cluster_endpoint()
        if connection_details:
            print("\n" + "="*80)
            print("DEPLOYMENT COMPLETED SUCCESSFULLY!")
            print("="*80)
            print("\nRedshift Cluster Connection Details:")
            print(f"  Host: {connection_details['host']}")
            print(f"  Port: {connection_details['port']}")
            print(f"  Database: {connection_details['database']}")
            print(f"  Username: {connection_details['username']}")
            print("  Password: (set via MASTER_PASSWORD env var or stored in Secrets Manager)")
            print(f"  Schema: {config.get_schema_name()}")
            print(f"  Status: {connection_details['status']}")
            
            print("\nYou can now connect to the cluster using:")
            print("- pgAdmin")
            print("- DBeaver")
            print("- AWS Redshift Query Editor")
            print("- Any PostgreSQL-compatible client")
            
            print(f"\nTo clean up resources when done:")
            print("  python scripts/cleanup.py")
            print("="*80)
        else:
            print("Warning: Could not retrieve connection details")
    except Exception as e:
        print(f"Warning: Error retrieving connection details: {e}")

def main():
    """Main deployment function"""
    parser = argparse.ArgumentParser(description='Deploy Redshift Sales Demo')
    parser.add_argument('--bucket-name', help='S3 bucket name (auto-generated if not provided)')
    parser.add_argument('--skip-generate-data', action='store_true', help='Skip data generation')
    parser.add_argument('--skip-cluster-creation', action='store_true', help='Skip cluster creation')
    parser.add_argument('--skip-data-upload', action='store_true', help='Skip data upload to S3')
    parser.add_argument('--config-file', help='Path to configuration file')
    args = parser.parse_args()
    
    # Load configuration
    if args.config_file:
        global config
        from config import Config
        config = Config(args.config_file)
    
    print("="*80)
    print("AMAZON REDSHIFT SALES DEMO DEPLOYMENT")
    print("="*80)
    print(f"AWS Region: {config.get_aws_region()}")
    print(f"Cluster ID: {config.get_cluster_identifier()}")
    print(f"Database: {config.get_database_name()}")
    print(f"Schema: {config.get_schema_name()}")
    print(f"IAM Role: {config.get_iam_role_name()}")
    
    # Determine bucket name
    bucket_name = args.bucket_name or config.get_s3_bucket_name()
    print(f"S3 Bucket: {bucket_name}")
    print("="*80)
    
    # Set environment variables for other scripts
    os.environ['S3_BUCKET_NAME'] = bucket_name
    
    try:
        # Step 1: Create S3 bucket
        print("\nStep 1: Creating S3 bucket...")
        if not create_s3_bucket(bucket_name):
            sys.exit(1)

        # Step 2: Create IAM role (scoped to the staging bucket)
        print("\nStep 2: Creating IAM role...")
        if not create_iam_role(bucket_name):
            sys.exit(1)
        
        # Step 3: Generate data
        if not args.skip_generate_data:
            print("\nStep 3: Generating sample data...")
            run_script('scripts/generate_data.py')
        else:
            print("\nStep 3: Skipping data generation (--skip-generate-data)")
        
        # Step 4: Create Redshift cluster
        if not args.skip_cluster_creation:
            print("\nStep 4: Creating Redshift cluster...")
            run_script('scripts/create_cluster.py')
        else:
            print("\nStep 4: Skipping cluster creation (--skip-cluster-creation)")
        
        # Step 5: Create schema and tables
        print("\nStep 5: Creating schema and tables...")
        run_script('scripts/execute_sql.py', ['scripts/create_schema.sql'])
        
        # Step 6: Upload data to S3
        if not args.skip_data_upload:
            print("\nStep 6: Uploading data to S3...")
            if not upload_data_to_s3(bucket_name):
                sys.exit(1)
        else:
            print("\nStep 6: Skipping data upload (--skip-data-upload)")
        
        # Step 7: Load dimension tables
        print("\nStep 7: Loading dimension tables...")
        run_script('scripts/load_dimension_tables.py')
        
        # Step 8: Load fact table
        print("\nStep 8: Loading fact table...")
        run_script('scripts/load_fact_sales.py')
        
        # Step 9: Verify data
        print("\nStep 9: Verifying data...")
        run_script('scripts/verify_data.py')
        
        # Print connection details
        print_connection_details()
        
    except KeyboardInterrupt:
        print("\nDeployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nDeployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

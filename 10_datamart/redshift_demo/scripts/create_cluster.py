#!/usr/bin/env python3
import boto3
import time
import sys
from config import config

def create_redshift_cluster():
    """
    Creates an Amazon Redshift cluster using RA3 node types for a sales data demonstration.
    """
    try:
        # Initialize Redshift client
        region = config.get_aws_region()
        redshift = boto3.client('redshift', region_name=region)
        
        # Get IAM role ARN
        iam = boto3.client('iam', region_name=region)
        role_name = config.get_iam_role_name()
        try:
            role_response = iam.get_role(RoleName=role_name)
            role_arn = role_response['Role']['Arn']
        except iam.exceptions.NoSuchEntityException:
            print(f"Error: IAM role '{role_name}' not found. Please create it first.")
            sys.exit(1)
        
        # Cluster parameters from configuration
        cluster_identifier = config.get_cluster_identifier()
        node_type = config.get('redshift.node_type')
        number_of_nodes = config.get('redshift.number_of_nodes')
        master_username = config.get_master_username()
        master_password = config.get_master_password()
        db_name = config.get_database_name()
        publicly_accessible = config.get('redshift.publicly_accessible')
        encrypted = config.get('redshift.encrypted')
        
        print(f"Creating Redshift cluster '{cluster_identifier}' with {number_of_nodes} {node_type} nodes...")
        print(f"Region: {region}")
        print(f"Database: {db_name}")
        print(f"Username: {master_username}")
        
        # Check if cluster already exists
        try:
            existing_clusters = redshift.describe_clusters(ClusterIdentifier=cluster_identifier)
            if existing_clusters['Clusters']:
                cluster = existing_clusters['Clusters'][0]
                if cluster['ClusterStatus'] == 'available':
                    print(f"Cluster '{cluster_identifier}' already exists and is available.")
                    print(f"Endpoint: {cluster['Endpoint']['Address']}:{cluster['Endpoint']['Port']}")
                    return cluster
                else:
                    print(f"Cluster '{cluster_identifier}' exists but is in state: {cluster['ClusterStatus']}")
                    print("Waiting for cluster to become available...")
        except redshift.exceptions.ClusterNotFoundFault:
            pass  # Cluster doesn't exist, we'll create it
        
        # Create the cluster
        response = redshift.create_cluster(
            ClusterIdentifier=cluster_identifier,
            NodeType=node_type,
            NumberOfNodes=number_of_nodes,
            MasterUsername=master_username,
            MasterUserPassword=master_password,
            DBName=db_name,
            ClusterType='multi-node' if number_of_nodes > 1 else 'single-node',
            PubliclyAccessible=publicly_accessible,
            Encrypted=encrypted,
            IamRoles=[role_arn],
            Tags=[
                {
                    'Key': 'Purpose',
                    'Value': 'Sales Demo'
                },
                {
                    'Key': 'Environment',
                    'Value': 'Demo'
                }
            ]
        )
        
        # Wait for the cluster to become available
        print("Waiting for cluster to become available. This may take 10-15 minutes...")
        waiter = redshift.get_waiter('cluster_available')
        waiter.wait(
            ClusterIdentifier=cluster_identifier,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60
            }
        )
        
        # Get cluster details
        response = redshift.describe_clusters(ClusterIdentifier=cluster_identifier)
        cluster = response['Clusters'][0]
        
        # Print connection information
        print("\n" + "="*80)
        print("Cluster created successfully!")
        print("="*80)
        print(f"Endpoint: {cluster['Endpoint']['Address']}:{cluster['Endpoint']['Port']}")
        print(f"Database: {db_name}")
        print(f"Username: {master_username}")
        print("Password: ********")
        print(f"IAM Role: {role_arn}")
        print("="*80)
        
        return cluster
        
    except Exception as e:
        print(f"Error creating Redshift cluster: {e}")
        sys.exit(1)

if __name__ == "__main__":
    create_redshift_cluster()

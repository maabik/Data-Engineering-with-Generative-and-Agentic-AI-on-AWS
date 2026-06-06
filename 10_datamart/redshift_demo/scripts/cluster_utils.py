#!/usr/bin/env python3
import os
import sys

import boto3

from config import config

def get_cluster_endpoint():
    """Get the cluster endpoint from AWS"""
    try:
        region = config.get_aws_region()
        cluster_identifier = config.get_cluster_identifier()
        
        redshift = boto3.client('redshift', region_name=region)
        response = redshift.describe_clusters(ClusterIdentifier=cluster_identifier)
        
        if not response['Clusters']:
            print(f"Error: Cluster '{cluster_identifier}' not found in region '{region}'")
            return None
            
        cluster = response['Clusters'][0]
        
        if cluster['ClusterStatus'] != 'available':
            print(f"Warning: Cluster is in state '{cluster['ClusterStatus']}', not 'available'")
        
        return {
            'host': cluster['Endpoint']['Address'],
            'port': cluster['Endpoint']['Port'],
            'database': config.get_database_name(),
            'username': config.get_master_username(),
            # Caller must source the password separately (env var / Secrets Manager).
            'password': os.environ.get('MASTER_PASSWORD'),
            'status': cluster['ClusterStatus']
        }
        
    except Exception as e:
        print(f"Error getting cluster details: {e}")
        return None

def get_iam_role_arn():
    """Get the IAM role ARN"""
    try:
        region = config.get_aws_region()
        role_name = config.get_iam_role_name()
        
        iam = boto3.client('iam', region_name=region)
        role_response = iam.get_role(RoleName=role_name)
        return role_response['Role']['Arn']
        
    except Exception as e:
        print(f"Error getting IAM role ARN: {e}")
        return None

def wait_for_cluster_available():
    """Wait for cluster to become available"""
    try:
        region = config.get_aws_region()
        cluster_identifier = config.get_cluster_identifier()
        
        redshift = boto3.client('redshift', region_name=region)
        
        print(f"Waiting for cluster '{cluster_identifier}' to become available...")
        waiter = redshift.get_waiter('cluster_available')
        waiter.wait(
            ClusterIdentifier=cluster_identifier,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60
            }
        )
        print("Cluster is now available!")
        return True
        
    except Exception as e:
        print(f"Error waiting for cluster: {e}")
        return False

if __name__ == "__main__":
    # Test the functions
    endpoint = get_cluster_endpoint()
    if endpoint:
        print("Cluster connection details:")
        print(f"  Host: {endpoint['host']}")
        print(f"  Port: {endpoint['port']}")
        print(f"  Database: {endpoint['database']}")
        print(f"  Username: {endpoint['username']}")
        print(f"  Status: {endpoint['status']}")
    
    role_arn = get_iam_role_arn()
    if role_arn:
        print(f"IAM Role ARN: {role_arn}")

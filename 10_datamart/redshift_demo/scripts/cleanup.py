#!/usr/bin/env python3
import boto3
import sys
import time
from botocore.exceptions import ClientError
from config import config

def delete_redshift_cluster():
    """Delete the Redshift cluster"""
    try:
        region = config.get_aws_region()
        cluster_identifier = config.get_cluster_identifier()
        
        print(f"Deleting Redshift cluster: {cluster_identifier} in region {region}...")
        redshift = boto3.client('redshift', region_name=region)
        
        # Check if cluster exists
        try:
            redshift.describe_clusters(ClusterIdentifier=cluster_identifier)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ClusterNotFound':
                print(f"Cluster {cluster_identifier} does not exist.")
                return True
            else:
                raise e
        
        # Delete the cluster
        response = redshift.delete_cluster(
            ClusterIdentifier=cluster_identifier,
            SkipFinalClusterSnapshot=True
        )
        
        print("Waiting for cluster to be deleted. This may take several minutes...")
        waiter = redshift.get_waiter('cluster_deleted')
        waiter.wait(
            ClusterIdentifier=cluster_identifier,
            WaiterConfig={
                'Delay': 30,
                'MaxAttempts': 60
            }
        )
        
        print("Redshift cluster deleted successfully.")
        return True
        
    except Exception as e:
        print(f"Error deleting Redshift cluster: {e}")
        return False

def delete_s3_bucket(bucket_name):
    """Delete the S3 bucket and all its contents"""
    try:
        region = config.get_aws_region()
        print(f"Deleting S3 bucket: {bucket_name} in region {region}...")
        
        s3 = boto3.resource('s3', region_name=region)
        bucket = s3.Bucket(bucket_name)
        
        # Check if bucket exists
        try:
            s3.meta.client.head_bucket(Bucket=bucket_name)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                print(f"Bucket {bucket_name} does not exist.")
                return True
            else:
                raise e
        
        # Delete all objects in the bucket
        print("Deleting all objects in the bucket...")
        bucket.objects.all().delete()
        
        # Delete the bucket
        bucket.delete()
        
        print("S3 bucket deleted successfully.")
        return True
        
    except Exception as e:
        print(f"Error deleting S3 bucket: {e}")
        return False

def delete_iam_role():
    """Delete the IAM role"""
    try:
        region = config.get_aws_region()
        role_name = config.get_iam_role_name()
        
        print(f"Deleting IAM role: {role_name}...")
        iam = boto3.client('iam', region_name=region)
        
        # Check if role exists
        try:
            iam.get_role(RoleName=role_name)
        except iam.exceptions.NoSuchEntityException:
            print(f"IAM role {role_name} does not exist.")
            return True
        
        # Detach policies from role
        try:
            attached_policies = iam.list_attached_role_policies(RoleName=role_name)
            for policy in attached_policies['AttachedPolicies']:
                print(f"Detaching policy: {policy['PolicyName']}")
                iam.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy['PolicyArn']
                )
        except Exception as e:
            print(f"Warning: Error detaching policies: {e}")
        
        # Delete the role
        iam.delete_role(RoleName=role_name)
        print("IAM role deleted successfully.")
        return True
        
    except Exception as e:
        print(f"Error deleting IAM role: {e}")
        return False

def main():
    """Main function to clean up resources"""
    print("="*80)
    print("AWS RESOURCE CLEANUP")
    print("="*80)
    print("This will delete the following resources:")
    print(f"- Redshift cluster: {config.get_cluster_identifier()}")
    print(f"- IAM role: {config.get_iam_role_name()}")
    print("- S3 bucket (you will be prompted for the name)")
    print("\nWARNING: This action cannot be undone!")
    
    confirm = input("\nDo you want to continue? (yes/no): ").lower().strip()
    if confirm not in ['yes', 'y']:
        print("Cleanup cancelled.")
        return
    
    success_count = 0
    total_operations = 3
    
    # Delete Redshift cluster
    print("\n" + "-"*80)
    if delete_redshift_cluster():
        success_count += 1
    
    # Delete IAM role
    print("\n" + "-"*80)
    if delete_iam_role():
        success_count += 1
    
    # Ask for S3 bucket name and delete it
    print("\n" + "-"*80)
    bucket_name = input("Enter the S3 bucket name to delete (or press Enter to skip): ").strip()
    if bucket_name:
        if delete_s3_bucket(bucket_name):
            success_count += 1
    else:
        print("Skipping S3 bucket deletion.")
        total_operations -= 1
    
    print("\n" + "="*80)
    print("CLEANUP SUMMARY")
    print("="*80)
    print(f"Successfully completed: {success_count}/{total_operations} operations")
    
    if success_count == total_operations:
        print("All cleanup operations completed successfully!")
    else:
        print("Some cleanup operations failed. Please check the output above.")
    
    print("="*80)

if __name__ == "__main__":
    main()

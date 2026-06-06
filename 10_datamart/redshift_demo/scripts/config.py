#!/usr/bin/env python3
import os
import secrets
import string
from pathlib import Path

import yaml

class Config:
    """Configuration loader for the Redshift Sales Demo project"""
    
    def __init__(self, config_file=None):
        if config_file is None:
            # Look for config.yaml in the parent directory
            config_file = Path(__file__).parent.parent / 'config.yaml'
        
        with open(config_file, 'r') as f:
            self._config = yaml.safe_load(f)
    
    def get(self, key_path, default=None):
        """Get configuration value using dot notation (e.g., 'aws.region')"""
        keys = key_path.split('.')
        value = self._config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def get_aws_region(self):
        return os.environ.get('AWS_REGION', self.get('aws.region', 'us-east-1'))
    
    def get_cluster_identifier(self):
        return os.environ.get('CLUSTER_IDENTIFIER', self.get('redshift.cluster_identifier'))
    
    def get_cluster_endpoint(self):
        """Generate cluster endpoint from cluster identifier and region"""
        cluster_id = self.get_cluster_identifier()
        region = self.get_aws_region()
        return f"{cluster_id}.{self._get_random_suffix()}.{region}.redshift.amazonaws.com"
    
    def _get_random_suffix(self):
        """Generate a random suffix for the cluster endpoint"""
        import random
        import string
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    
    def get_database_name(self):
        return os.environ.get('DATABASE_NAME', self.get('redshift.database_name'))
    
    def get_master_username(self):
        return os.environ.get('MASTER_USERNAME', self.get('redshift.master_username'))
    
    def get_master_password(self, *, generate_if_missing=False):
        password = os.environ.get('MASTER_PASSWORD') or self.get('redshift.master_password')
        if password:
            return password
        if generate_if_missing:
            return generate_redshift_password()
        raise RuntimeError(
            "Redshift master password not set. Provide MASTER_PASSWORD env var "
            "or call get_master_password(generate_if_missing=True)."
        )
    
    def get_iam_role_name(self):
        return os.environ.get('IAM_ROLE_NAME', self.get('iam.role_name'))
    
    def get_s3_bucket_name(self):
        bucket_name = os.environ.get('S3_BUCKET_NAME')
        if bucket_name:
            return bucket_name
        
        import time
        prefix = self.get('s3.bucket_prefix')
        return f"{prefix}-{int(time.time())}"
    
    def get_schema_name(self):
        return os.environ.get('SCHEMA_NAME', self.get('schema.name'))

def generate_redshift_password(length: int = 24) -> str:
    """Generate a Redshift-compliant master password.

    Redshift requires 8-64 chars, at least one uppercase, one lowercase, one
    digit, no '/', '@', '"', '\'', ' ', or backslash.
    """
    if length < 8:
        raise ValueError("password length must be >= 8")
    alphabet = string.ascii_letters + string.digits + "!#$%&()*+,-.:;<=>?[]^_{|}~"
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)):
            return password


# Global config instance
config = Config()

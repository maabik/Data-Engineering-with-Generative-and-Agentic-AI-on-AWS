#!/usr/bin/env python3
import psycopg2
import sys
import os
from config import config
from cluster_utils import get_cluster_endpoint, get_iam_role_arn

def load_dimension_tables(connection_details, bucket_name, iam_role_arn):
    """Load dimension tables from S3 to Redshift"""
    try:
        # Connect to Redshift
        conn = psycopg2.connect(
            host=connection_details['host'],
            port=connection_details['port'],
            dbname=connection_details['database'],
            user=connection_details['username'],
            password=connection_details['password']
        )
        print("Connected to Redshift successfully")
        
        cursor = conn.cursor()
        schema_name = config.get_schema_name()
        region = config.get_aws_region()
        
        # List of tables to load with standard COPY
        standard_tables = [
            'dim_time', 'dim_region', 'dim_district', 'dim_sales_territory',
            'dim_zipcode', 'dim_dma', 'dim_account', 'dim_product'
        ]
        
        # Load standard tables
        for table in standard_tables:
            print(f"Loading data into {table}...")
            copy_cmd = f"""
            COPY {schema_name}.{table}
            FROM 's3://{bucket_name}/data/{table}.csv'
            IAM_ROLE '{iam_role_arn}'
            FORMAT CSV
            IGNOREHEADER 1
            REGION '{region}';
            """
            try:
                cursor.execute(copy_cmd)
                conn.commit()
                print(f"Successfully loaded data into {table}")
            except Exception as e:
                print(f"Error loading data into {table}: {e}")
                conn.rollback()
        
        # Load sales_rep table with special handling for manager_key
        print("Loading data into dim_sales_rep...")
        try:
            # Create temporary table
            cursor.execute(f"""
            CREATE TEMP TABLE temp_sales_rep (
                sales_rep_key INT,
                sales_rep_id VARCHAR(20),
                first_name VARCHAR(50),
                last_name VARCHAR(50),
                email VARCHAR(100),
                hire_date DATE,
                territory_key INT,
                manager_key VARCHAR(20),
                active_flag BOOLEAN,
                created_date TIMESTAMP,
                updated_date TIMESTAMP
            );
            """)
            
            # Load data into temp table
            temp_copy_cmd = f"""
            COPY temp_sales_rep
            FROM 's3://{bucket_name}/data/dim_sales_rep.csv'
            IAM_ROLE '{iam_role_arn}'
            FORMAT CSV
            IGNOREHEADER 1
            REGION '{region}'
            NULL AS ''
            ACCEPTINVCHARS AS ' '
            TRIMBLANKS;
            """
            cursor.execute(temp_copy_cmd)
            
            # Insert from temp table to actual table with CAST
            cursor.execute(f"""
            INSERT INTO {schema_name}.dim_sales_rep
            SELECT 
                sales_rep_key,
                sales_rep_id,
                first_name,
                last_name,
                email,
                hire_date,
                territory_key,
                CASE 
                    WHEN manager_key = '' THEN NULL
                    ELSE CAST(CAST(manager_key AS FLOAT) AS INT)
                END,
                active_flag,
                created_date,
                updated_date
            FROM temp_sales_rep;
            """)
            
            conn.commit()
            print("Successfully loaded data into dim_sales_rep")
        except Exception as e:
            print(f"Error loading data into dim_sales_rep: {e}")
            conn.rollback()
        
        cursor.close()
        conn.close()
        print("Dimension tables loading completed")
        return True
    except Exception as e:
        print(f"Error loading dimension tables: {e}")
        return False

if __name__ == "__main__":
    # Get connection details
    connection_details = get_cluster_endpoint()
    if not connection_details:
        print("Failed to get cluster connection details")
        sys.exit(1)
    
    # Get bucket name from environment or use default
    bucket_name = os.environ.get('S3_BUCKET_NAME')
    if not bucket_name:
        bucket_name = config.get_s3_bucket_name()
        print(f"Using generated bucket name: {bucket_name}")
    
    # Get IAM role ARN
    iam_role_arn = get_iam_role_arn()
    if not iam_role_arn:
        print("Failed to get IAM role ARN")
        sys.exit(1)
    
    print(f"Using IAM role: {iam_role_arn}")
    print(f"Using S3 bucket: {bucket_name}")
    
    if load_dimension_tables(connection_details, bucket_name, iam_role_arn):
        print("Dimension tables loading completed successfully")
    else:
        print("Dimension tables loading failed")
        sys.exit(1)

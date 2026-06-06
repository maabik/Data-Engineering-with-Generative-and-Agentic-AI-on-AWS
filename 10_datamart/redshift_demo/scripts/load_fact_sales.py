#!/usr/bin/env python3
import psycopg2
from psycopg2 import sql
import sys
import os
from config import config
from cluster_utils import get_cluster_endpoint, get_iam_role_arn

def load_fact_sales(connection_details, bucket_name, iam_role_arn):
    """Load fact_sales data from S3 to Redshift"""
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
        schema_ident = sql.Identifier(schema_name)
        region = config.get_aws_region()
        
        # Create temporary table
        print("Creating temporary table...")
        cursor.execute("""
        DROP TABLE IF EXISTS temp_fact_sales;
        CREATE TABLE temp_fact_sales (
            order_id VARCHAR(20),
            order_date_key INT,
            ship_date_key VARCHAR(20),
            account_key INT,
            product_key INT,
            sales_rep_key INT,
            territory_key INT,
            district_key INT,
            region_key INT,
            zipcode_key INT,
            dma_key VARCHAR(20),
            quantity INT,
            unit_price DECIMAL(15,2),
            unit_cost DECIMAL(15,2),
            discount_pct DECIMAL(5,2),
            sales_amount DECIMAL(15,2),
            profit_amount DECIMAL(15,2),
            return_flag BOOLEAN,
            online_order_flag BOOLEAN,
            created_date TIMESTAMP,
            updated_date TIMESTAMP
        );
        """)
        conn.commit()
        
        # Load data into temp table
        print("Loading data into temporary table...")
        s3_uri = f"s3://{bucket_name}/data/fact_sales.csv"
        temp_copy_cmd = sql.SQL("""
        COPY temp_fact_sales
        FROM {s3_uri}
        IAM_ROLE {iam_role_arn}
        FORMAT CSV
        IGNOREHEADER 1
        REGION {region}
        NULL AS ''
        ACCEPTINVCHARS AS ' '
        TRIMBLANKS;
        """).format(
            s3_uri=sql.Literal(s3_uri),
            iam_role_arn=sql.Literal(iam_role_arn),
            region=sql.Literal(region),
        )
        cursor.execute(temp_copy_cmd)
        conn.commit()

        # Insert from temp table to actual table with CAST
        print("Inserting data into fact_sales table...")
        cursor.execute(sql.SQL("""
        INSERT INTO {schema}.fact_sales (
            order_id, order_date_key, ship_date_key, account_key, product_key,
            sales_rep_key, territory_key, district_key, region_key, zipcode_key,
            dma_key, quantity, unit_price, unit_cost, discount_pct, sales_amount,
            profit_amount, return_flag, online_order_flag, created_date, updated_date
        )
        SELECT
            order_id,
            order_date_key,
            CASE
                WHEN ship_date_key = '' THEN NULL
                ELSE CAST(CAST(ship_date_key AS FLOAT) AS INT)
            END,
            account_key,
            product_key,
            sales_rep_key,
            territory_key,
            district_key,
            region_key,
            zipcode_key,
            CASE
                WHEN dma_key = '' THEN NULL
                ELSE CAST(CAST(dma_key AS FLOAT) AS INT)
            END,
            quantity,
            unit_price,
            unit_cost,
            discount_pct,
            sales_amount,
            profit_amount,
            return_flag,
            online_order_flag,
            created_date,
            updated_date
        FROM temp_fact_sales;
        """).format(schema=schema_ident))
        conn.commit()

        # Get count of loaded rows
        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM {schema}.fact_sales;").format(schema=schema_ident)
        )
        count = cursor.fetchone()[0]
        print(f"Successfully loaded {count} rows into fact_sales")
        
        # Clean up
        cursor.execute("DROP TABLE IF EXISTS temp_fact_sales;")
        conn.commit()
        
        cursor.close()
        conn.close()
        print("Fact sales loading completed")
        return True
    except Exception as e:
        print(f"Error loading fact_sales: {e}")
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
    
    if load_fact_sales(connection_details, bucket_name, iam_role_arn):
        print("Fact sales loading completed successfully")
    else:
        print("Fact sales loading failed")
        sys.exit(1)

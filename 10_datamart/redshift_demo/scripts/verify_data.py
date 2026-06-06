#!/usr/bin/env python3
import psycopg2
from psycopg2 import sql
import sys
from config import config
from cluster_utils import get_cluster_endpoint

def verify_data(connection_details):
    """Verify that data has been loaded correctly"""
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
        
        # List of tables to check
        tables = [
            'dim_time', 'dim_region', 'dim_district', 'dim_sales_territory',
            'dim_zipcode', 'dim_dma', 'dim_account', 'dim_product', 
            'dim_sales_rep', 'fact_sales'
        ]
        
        print("\n" + "="*80)
        print("DATA VERIFICATION REPORT")
        print("="*80)
        
        # Check row counts for each table
        total_rows = 0
        for table in tables:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{};").format(
                    sql.Identifier(schema_name), sql.Identifier(table)
                )
            )
            count = cursor.fetchone()[0]
            print(f"Table {schema_name}.{table}: {count:,} rows")
            total_rows += count
        
        print(f"\nTotal rows across all tables: {total_rows:,}")
        
        # Run a sample query to verify data relationships
        print("\n" + "="*80)
        print("SAMPLE ANALYTICAL QUERY")
        print("="*80)
        
        schema_ident = sql.Identifier(schema_name)
        query = sql.SQL("""
        SELECT
            dt.year_actual,
            dt.month_name,
            dr.region_name,
            COUNT(DISTINCT fs.order_id) as num_orders,
            SUM(fs.sales_amount) as total_sales,
            SUM(fs.profit_amount) as total_profit
        FROM
            {schema}.fact_sales fs
            JOIN {schema}.dim_time dt ON fs.order_date_key = dt.time_key
            JOIN {schema}.dim_region dr ON fs.region_key = dr.region_key
        WHERE
            fs.return_flag = FALSE
        GROUP BY
            dt.year_actual,
            dt.month_name,
            dr.region_name
        ORDER BY
            dt.year_actual,
            dt.month_name,
            total_sales DESC
        LIMIT 10;
        """).format(schema=schema_ident)

        cursor.execute(query)
        rows = cursor.fetchall()
        
        print("\nTop 10 Monthly Sales by Region:")
        print("-" * 80)
        print(f"{'Year':<6} {'Month':<12} {'Region':<25} {'Orders':<8} {'Sales':<15} {'Profit':<15}")
        print("-" * 80)
        
        for row in rows:
            year, month, region, orders, sales, profit = row
            print(f"{year:<6} {month:<12} {region[:24]:<25} {orders:<8} ${sales:>12,.2f} ${profit:>12,.2f}")
        
        # Additional verification queries
        print("\n" + "="*80)
        print("DATA QUALITY CHECKS")
        print("="*80)
        
        # Check for null values in key fields
        cursor.execute(sql.SQL("""
        SELECT
            COUNT(*) as total_sales,
            COUNT(CASE WHEN order_id IS NULL THEN 1 END) as null_order_ids,
            COUNT(CASE WHEN sales_amount IS NULL THEN 1 END) as null_sales_amounts,
            COUNT(CASE WHEN order_date_key IS NULL THEN 1 END) as null_order_dates
        FROM {schema}.fact_sales;
        """).format(schema=schema_ident))
        
        quality_check = cursor.fetchone()
        total_sales, null_orders, null_amounts, null_dates = quality_check
        
        print(f"Total sales records: {total_sales:,}")
        print(f"Records with null order_id: {null_orders:,}")
        print(f"Records with null sales_amount: {null_amounts:,}")
        print(f"Records with null order_date_key: {null_dates:,}")
        
        # Check date range
        cursor.execute(sql.SQL("""
        SELECT
            MIN(dt.date_actual) as min_date,
            MAX(dt.date_actual) as max_date
        FROM {schema}.fact_sales fs
        JOIN {schema}.dim_time dt ON fs.order_date_key = dt.time_key;
        """).format(schema=schema_ident))
        
        date_range = cursor.fetchone()
        min_date, max_date = date_range
        print(f"Date range: {min_date} to {max_date}")
        
        # Check for referential integrity
        cursor.execute(sql.SQL("""
        SELECT
            COUNT(*) as orphaned_sales
        FROM {schema}.fact_sales fs
        LEFT JOIN {schema}.dim_time dt ON fs.order_date_key = dt.time_key
        WHERE dt.time_key IS NULL;
        """).format(schema=schema_ident))
        
        orphaned = cursor.fetchone()[0]
        print(f"Orphaned sales records (no matching time dimension): {orphaned:,}")
        
        print("\n" + "="*80)
        print("VERIFICATION COMPLETED SUCCESSFULLY!")
        print("="*80)
        
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error verifying data: {e}")
        return False

if __name__ == "__main__":
    # Get connection details
    connection_details = get_cluster_endpoint()
    if not connection_details:
        print("Failed to get cluster connection details")
        sys.exit(1)
    
    print(f"Connecting to cluster: {connection_details['host']}")
    print(f"Database: {connection_details['database']}")
    print(f"Status: {connection_details['status']}")
    
    if connection_details['status'] != 'available':
        print("Warning: Cluster is not in 'available' state")
    
    if verify_data(connection_details):
        print("\nData verification completed successfully")
    else:
        print("\nData verification failed")
        sys.exit(1)

#!/usr/bin/env python3
import psycopg2
from psycopg2 import sql
import sys
from config import config
from cluster_utils import get_cluster_endpoint

def execute_sql_file(sql_file, connection_details):
    """Execute SQL commands from a file"""
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
        
        # Read SQL file
        with open(sql_file, 'r') as f:
            sql = f.read()
        
        # Replace schema placeholder with actual schema name
        schema_name = config.get_schema_name()
        sql = sql.replace('sales', schema_name)
        
        # First, create the schema
        cursor = conn.cursor()
        cursor.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(sql.Identifier(schema_name))
        )
        conn.commit()
        print(f"Created schema: {schema_name}")
        
        # Split SQL into individual statements
        statements = sql.split(';')
        
        # Execute each statement
        executed_count = 0
        for statement in statements:
            # Skip empty statements and comments
            statement = statement.strip()
            if statement and not statement.startswith('--'):
                try:
                    cursor.execute(statement)
                    conn.commit()
                    executed_count += 1
                    
                    # Extract table/view name for logging
                    if 'CREATE TABLE' in statement.upper():
                        table_name = statement.split()[2] if len(statement.split()) > 2 else "unknown"
                        print(f"Created table: {table_name}")
                    elif 'CREATE VIEW' in statement.upper():
                        view_name = statement.split()[2] if len(statement.split()) > 2 else "unknown"
                        print(f"Created view: {view_name}")
                    else:
                        print(f"Executed statement ({len(statement)} chars)")
                        
                except Exception as e:
                    print(f"Warning: Error executing statement: {e}")
                    print(f"Statement: {statement[:100]}...")
        
        cursor.close()
        print(f"\nExecuted {executed_count} SQL statements from: {sql_file}")
        conn.close()
        return True
    except Exception as e:
        print(f"Error executing SQL file {sql_file}: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python execute_sql.py <sql_file>")
        sys.exit(1)
        
    sql_file = sys.argv[1]
    
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
    
    if execute_sql_file(sql_file, connection_details):
        print("SQL execution completed successfully")
    else:
        print("SQL execution failed")
        sys.exit(1)

#!/usr/bin/env python3
import boto3
import argparse
import uuid
from datetime import datetime

def update_trade(trade_id, price=None, quantity=None):
    """
    Update an existing trade in the S3 Tables (Iceberg) table
    Demonstrates transactional updates to the data lake
    """
    athena_client = boto3.client('athena')
    
    # Build the SET clause for the UPDATE statement
    set_clauses = []
    if price is not None:
        set_clauses.append(f"price = {price}")
    if quantity is not None:
        set_clauses.append(f"quantity = {quantity}")
        # Also update the total_value if quantity changes
        if price is None:
            set_clauses.append(f"total_value = price * {quantity}")
        else:
            set_clauses.append(f"total_value = {price} * {quantity}")
    
    if not set_clauses:
        print("No updates specified. Please provide at least one of --price or --quantity")
        return
    
    set_clause = ", ".join(set_clauses)
    
    # Prepare the SQL query to update data in the S3 Tables table
    query = f"""
    UPDATE stock_trading_db.trades 
    SET {set_clause}
    WHERE trade_id = '{trade_id}'
    """
    
    print(f"Executing query: {query}")
    
    # Execute the query
    response = athena_client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={
            'Database': 'stock_trading_db'
        },
        ResultConfiguration={
            'OutputLocation': 's3://stocktradingstack-stocktradingbucket4c781ddd-ihdf12wko7ia/query-results/'
        }
    )
    
    query_execution_id = response['QueryExecutionId']
    print(f"Query execution ID: {query_execution_id}")
    
    # Wait for the query to complete
    while True:
        response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
        state = response['QueryExecution']['Status']['State']
        
        if state in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
            break
    
    if state == 'SUCCEEDED':
        print(f"Trade {trade_id} updated successfully")
    else:
        error = response['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
        print(f"Update failed: {error}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Update a trade in the S3 Tables')
    parser.add_argument('trade_id', help='ID of the trade to update')
    parser.add_argument('--price', type=float, help='New price for the trade')
    parser.add_argument('--quantity', type=int, help='New quantity for the trade')
    
    args = parser.parse_args()
    update_trade(args.trade_id, args.price, args.quantity)

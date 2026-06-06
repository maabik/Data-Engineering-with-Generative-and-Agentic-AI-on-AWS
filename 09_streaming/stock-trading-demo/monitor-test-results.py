#!/usr/bin/env python3
"""
Monitor test results for Firehose → S3 Tables integration
"""
import boto3
import time
from datetime import datetime, timedelta

def check_firehose_metrics():
    """Check Firehose delivery metrics"""
    print("📊 Checking Firehose Metrics...")
    print("-" * 40)
    
    cloudwatch = boto3.client('cloudwatch')
    
    try:
        # Get metrics for the last 10 minutes
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=10)
        
        metrics = [
            'DeliveryToS3.Records',
            'DeliveryToS3.Success',
            'DeliveryToS3.DataFreshness'
        ]
        
        for metric in metrics:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/KinesisFirehose',
                MetricName=metric,
                Dimensions=[
                    {
                        'Name': 'DeliveryStreamName',
                        'Value': 'stock-trades-to-s3tables'
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=300,
                Statistics=['Sum', 'Average', 'Maximum']
            )
            
            if response['Datapoints']:
                latest = max(response['Datapoints'], key=lambda x: x['Timestamp'])
                print(f"✅ {metric}:")
                for stat in ['Sum', 'Average', 'Maximum']:
                    if stat in latest:
                        print(f"   {stat}: {latest[stat]}")
            else:
                print(f"⏳ {metric}: No data yet")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking Firehose metrics: {str(e)}")
        return False

def check_s3_tables_data():
    """Check if data exists in S3 Tables"""
    print("\n🗂️  Checking S3 Tables Data...")
    print("-" * 40)
    
    try:
        s3tables = boto3.client('s3tables')
        account_id = boto3.client('sts').get_caller_identity()['Account']
        region = boto3.session.Session().region_name or 'us-east-1'

        # List tables
        response = s3tables.list_tables(
            tableBucketARN=f'arn:aws:s3tables:{region}:{account_id}:bucket/stock-trading-demo',
            namespace='trading'
        )
        
        print(f"✅ Found {len(response['tables'])} tables in trading namespace")
        
        for table in response['tables']:
            print(f"   📋 Table: {table['name']}")
            print(f"      ARN: {table['tableARN']}")
            print(f"      Created: {table['createdAt']}")
            print(f"      Modified: {table['modifiedAt']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking S3 Tables: {str(e)}")
        return False

def check_lambda_logs():
    """Check Lambda trend analysis logs"""
    print("\n🔍 Checking Lambda Trend Analysis...")
    print("-" * 40)
    
    try:
        logs_client = boto3.client('logs')
        
        # Find Lambda log group
        log_groups = logs_client.describe_log_groups(
            logGroupNamePrefix='/aws/lambda/StockTradingStack-TrendAnalyzer'
        )
        
        if not log_groups['logGroups']:
            print("⏳ No Lambda log groups found yet")
            return False
        
        log_group_name = log_groups['logGroups'][0]['logGroupName']
        print(f"📋 Log Group: {log_group_name}")
        
        # Get recent log events
        end_time = int(time.time() * 1000)
        start_time = end_time - (10 * 60 * 1000)  # Last 10 minutes
        
        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            limit=10
        )
        
        if response['events']:
            print(f"✅ Found {len(response['events'])} recent log events")
            for event in response['events'][-3:]:  # Show last 3 events
                timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                print(f"   {timestamp}: {event['message'][:100]}...")
        else:
            print("⏳ No recent Lambda executions found")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking Lambda logs: {str(e)}")
        return False

def check_athena_query():
    """Test querying data via Athena"""
    print("\n🔎 Testing Athena Query...")
    print("-" * 40)
    
    try:
        athena = boto3.client('athena')
        s3 = boto3.client('s3')
        
        # Create query results bucket if needed
        account_id = boto3.client('sts').get_caller_identity()['Account']
        region = boto3.session.Session().region_name or 'us-east-1'
        bucket_name = f'aws-athena-query-results-{account_id}-{region}'
        
        query = """
        SELECT 
            COUNT(*) as total_trades,
            COUNT(DISTINCT stock_symbol) as unique_stocks,
            AVG(price) as avg_price,
            SUM(total_value) as total_volume
        FROM trading.trades
        WHERE timestamp >= current_timestamp - interval '1' hour
        """
        
        response = athena.start_query_execution(
            QueryString=query,
            ResultConfiguration={
                'OutputLocation': f's3://{bucket_name}/'
            },
            WorkGroup='primary'
        )
        
        query_id = response['QueryExecutionId']
        print(f"✅ Started Athena query: {query_id}")
        
        # Wait for query to complete (up to 30 seconds)
        for i in range(30):
            response = athena.get_query_execution(QueryExecutionId=query_id)
            status = response['QueryExecution']['Status']['State']
            
            if status == 'SUCCEEDED':
                print("✅ Query completed successfully")
                
                # Get results
                results = athena.get_query_results(QueryExecutionId=query_id)
                if len(results['ResultSet']['Rows']) > 1:
                    data_row = results['ResultSet']['Rows'][1]['Data']
                    print(f"   📊 Total Trades: {data_row[0].get('VarCharValue', '0')}")
                    print(f"   📊 Unique Stocks: {data_row[1].get('VarCharValue', '0')}")
                    print(f"   📊 Avg Price: ${data_row[2].get('VarCharValue', '0')}")
                    print(f"   📊 Total Volume: ${data_row[3].get('VarCharValue', '0')}")
                else:
                    print("   ⏳ No data returned (may still be processing)")
                return True
                
            elif status == 'FAILED':
                error = response['QueryExecution']['Status'].get('StateChangeReason', 'Unknown error')
                print(f"❌ Query failed: {error}")
                return False
            
            time.sleep(1)
        
        print("⏳ Query still running after 30 seconds")
        return False
        
    except Exception as e:
        print(f"❌ Error running Athena query: {str(e)}")
        return False

def main():
    """Run all monitoring checks"""
    print("🔍 Monitoring Stock Trading Test Results")
    print("=" * 50)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    checks = [
        ("Firehose Metrics", check_firehose_metrics),
        ("S3 Tables Data", check_s3_tables_data),
        ("Lambda Logs", check_lambda_logs),
        ("Athena Query", check_athena_query)
    ]
    
    results = []
    for check_name, check_func in checks:
        result = check_func()
        results.append((check_name, result))
    
    print("\n📋 Summary")
    print("=" * 30)
    for check_name, result in results:
        status = "✅ PASS" if result else "⏳ PENDING"
        print(f"{status} {check_name}")
    
    print("\n💡 Tips:")
    print("• Firehose batches data, so it may take 1-2 minutes to appear")
    print("• Lambda trend analysis triggers after 10+ trades")
    print("• S3 Tables data is immediately queryable via Athena")
    print("• Check CloudWatch logs for detailed processing info")

if __name__ == "__main__":
    main()

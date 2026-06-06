#!/usr/bin/env python3
"""
Monitor KDS-s3tables-trades Firehose stream and S3 Tables integration
"""
import boto3
import time
from datetime import datetime, timedelta


def _account_and_region():
    region = boto3.session.Session().region_name or 'us-east-1'
    account_id = boto3.client('sts').get_caller_identity()['Account']
    return account_id, region

def check_firehose_status():
    """Check Firehose delivery stream status"""
    print("🚀 Checking KDS-s3tables-trades Firehose Status...")
    print("-" * 50)
    
    try:
        firehose = boto3.client('firehose')
        
        response = firehose.describe_delivery_stream(
            DeliveryStreamName='KDS-s3tables-trades'
        )
        
        stream = response['DeliveryStreamDescription']
        print(f"✅ Stream Status: {stream['DeliveryStreamStatus']}")
        print(f"✅ Stream Type: {stream['DeliveryStreamType']}")
        print(f"✅ Created: {stream['CreateTimestamp']}")
        
        # Check destination configuration
        dest = stream['Destinations'][0]['IcebergDestinationDescription']
        table_config = dest['DestinationTableConfigurationList'][0]
        
        print(f"✅ Target Database: {table_config['DestinationDatabaseName']}")
        print(f"✅ Target Table: {table_config['DestinationTableName']}")
        print(f"✅ Unique Keys: {table_config['UniqueKeys']}")
        print(f"✅ Buffer Size: {dest['BufferingHints']['SizeInMBs']}MB")
        print(f"✅ Buffer Time: {dest['BufferingHints']['IntervalInSeconds']}s")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking Firehose: {str(e)}")
        return False

def check_firehose_logs():
    """Check Firehose processing logs"""
    print("\n📋 Checking Firehose Processing Logs...")
    print("-" * 50)
    
    try:
        logs_client = boto3.client('logs')
        
        # Check DestinationDelivery logs
        response = logs_client.get_log_events(
            logGroupName='/aws/kinesisfirehose/KDS-s3tables-trades',
            logStreamName='DestinationDelivery',
            limit=5,
            startFromHead=False
        )
        
        if response['events']:
            print(f"✅ Found {len(response['events'])} recent delivery events:")
            for event in response['events']:
                timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                print(f"   {timestamp}: {event['message'][:100]}...")
        else:
            print("⏳ No delivery events yet (data may still be buffering)")
        
        # Check BackupDelivery logs
        response = logs_client.get_log_events(
            logGroupName='/aws/kinesisfirehose/KDS-s3tables-trades',
            logStreamName='BackupDelivery',
            limit=5,
            startFromHead=False
        )
        
        if response['events']:
            print(f"⚠️  Found {len(response['events'])} backup events (potential errors):")
            for event in response['events']:
                timestamp = datetime.fromtimestamp(event['timestamp'] / 1000)
                print(f"   {timestamp}: {event['message'][:100]}...")
        else:
            print("✅ No backup events (no processing errors)")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking logs: {str(e)}")
        return False

def check_s3_tables_data():
    """Check if data exists in S3 Tables"""
    print("\n🗂️  Checking S3 Tables Data...")
    print("-" * 50)
    
    try:
        s3tables = boto3.client('s3tables')
        account_id, region = _account_and_region()

        # Get table info
        response = s3tables.get_table(
            tableBucketARN=f'arn:aws:s3tables:{region}:{account_id}:bucket/stock-trading-demo',
            namespace='trading',
            name='trades'
        )
        
        print(f"✅ Table Found: {response['name']}")
        print(f"✅ Table ARN: {response['tableARN']}")
        print(f"✅ Created: {response['createdAt']}")
        print(f"✅ Modified: {response['modifiedAt']}")
        print(f"✅ Format: {response['format']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error checking S3 Tables: {str(e)}")
        return False

def run_athena_test_query():
    """Run a test query via Athena"""
    print("\n🔎 Running Athena Test Query...")
    print("-" * 50)
    
    try:
        athena = boto3.client('athena')
        account_id, region = _account_and_region()

        query = """
        SELECT
            trade_id,
            stock_symbol,
            price,
            quantity,
            trade_type,
            timestamp
        FROM trading.trades
        WHERE trade_id LIKE 'KDS-%' OR trade_id LIKE 'UPSERT-KDS%'
        ORDER BY timestamp DESC
        LIMIT 10
        """

        response = athena.start_query_execution(
            QueryString=query,
            ResultConfiguration={
                'OutputLocation': f's3://aws-athena-query-results-{account_id}-{region}/'
            },
            WorkGroup='primary'
        )
        
        query_id = response['QueryExecutionId']
        print(f"✅ Started query: {query_id}")
        
        # Wait for query completion (up to 30 seconds)
        for i in range(30):
            response = athena.get_query_execution(QueryExecutionId=query_id)
            status = response['QueryExecution']['Status']['State']
            
            if status == 'SUCCEEDED':
                print("✅ Query completed successfully")
                
                # Get results
                results = athena.get_query_results(QueryExecutionId=query_id)
                rows = results['ResultSet']['Rows']
                
                if len(rows) > 1:  # Skip header row
                    print(f"✅ Found {len(rows)-1} trades in S3 Tables:")
                    for i, row in enumerate(rows[1:6], 1):  # Show first 5 results
                        data = [col.get('VarCharValue', '') for col in row['Data']]
                        print(f"   {i}. {data[0]} - {data[1]} ${data[2]} x {data[3]} ({data[4]})")
                else:
                    print("⏳ No data found yet (may still be processing)")
                
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
    print("🔍 Monitoring KDS-s3tables-trades Pipeline")
    print("=" * 50)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    checks = [
        ("Firehose Status", check_firehose_status),
        ("Firehose Logs", check_firehose_logs),
        ("S3 Tables Data", check_s3_tables_data),
        ("Athena Query", run_athena_test_query)
    ]
    
    results = []
    for check_name, check_func in checks:
        result = check_func()
        results.append((check_name, result))
    
    print("\n📊 Summary")
    print("=" * 30)
    for check_name, result in results:
        status = "✅ PASS" if result else "⏳ PENDING"
        print(f"{status} {check_name}")
    
    print("\n💡 Pipeline Status:")
    if all(result for _, result in results):
        print("🎉 All systems operational! Data flowing to S3 Tables.")
    else:
        print("⏳ Pipeline warming up. Firehose buffers data for up to 5 minutes.")
        print("   Check again in a few minutes for complete results.")
    
    print("\n🔍 Useful Commands:")
    print("• Monitor logs: aws logs tail /aws/kinesisfirehose/KDS-s3tables-trades --follow")
    print("• Query data: SELECT COUNT(*) FROM trading.trades;")
    print("• Check UPSERT: SELECT * FROM trading.trades WHERE trade_id = 'UPSERT-KDS-001';")

if __name__ == "__main__":
    main()

import boto3
import requests

def lambda_handler(event, context):
    api_url = 'https://api.coindesk.com/v1/bpi/currentprice.json'
    response = requests.get(api_url)
    data = response.json()
    
    bucket_name = 'data-ingestion-lambda-to-s3-us-east-1'  # Replace with your S3 bucket name
    key = 'bitcoin_prices.json'  # Replace with desired S3 key
    
    s3 = boto3.client('s3')
    s3.put_object(Bucket=bucket_name, Key=key, Body=response.text)
    
    return {
        'statusCode': 200,
        'body': 'JSON file successfully fetched and uploaded.'
    }

import datetime
import logging
import os
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET_NAME = os.environ["BUCKET_NAME"]
API_URL = os.environ.get("API_URL", "https://api.coindesk.com/v1/bpi/currentprice.json")

s3 = boto3.client("s3")


def lambda_handler(event, context):
    now = datetime.datetime.utcnow()
    key = (
        f"bitcoin_prices/"
        f"year={now.year}/month={now.month:02d}/"
        f"day={now.day:02d}/hour={now.hour:02d}/data.json"
    )

    with urllib.request.urlopen(API_URL, timeout=10) as resp:  # noqa: S310
        body = resp.read()

    s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=body, ContentType="application/json")
    logger.info("Uploaded %s bytes to s3://%s/%s", len(body), BUCKET_NAME, key)

    return {"statusCode": 200, "body": f"Uploaded to s3://{BUCKET_NAME}/{key}"}

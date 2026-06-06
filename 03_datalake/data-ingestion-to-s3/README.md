# Data Ingestion to S3 (Lambda)

A 19-line Lambda function that fetches a JSON document from a public HTTP
API and writes it to S3. The minimal shape used as the warm-up example for
the chapter 03 "ingest to the lake" section.

## File

| File | What it does |
|---|---|
| `data_ingestion_to_s3.py` | Lambda handler — `requests.get` against the CoinDesk Bitcoin price API, then `s3.put_object` into a hard-coded bucket. |

## Prerequisites

- An S3 bucket. The handler hard-codes `data-ingestion-lambda-to-s3-us-east-1` —
  change the literal in the source to match your bucket before deploying.
- A Lambda execution role with `s3:PutObject` on the bucket and the
  basic CloudWatch Logs permissions.
- The `requests` library — Lambda's bundled runtime does not include it.
  Either deploy with a `requests` Lambda layer or rewrite the call to use
  `urllib3` (see `05_bigdata/lambda-api-ingestion/lambda-api-ingestion.py`
  for a `urllib3` example).

## Deploy

```bash
zip function.zip data_ingestion_to_s3.py

aws lambda create-function \
    --function-name data-ingestion-to-s3 \
    --runtime python3.12 \
    --role arn:aws:iam::<account>:role/<lambda-role> \
    --handler data_ingestion_to_s3.lambda_handler \
    --zip-file fileb://function.zip
```

Invoke it once to verify:

```bash
aws lambda invoke --function-name data-ingestion-to-s3 /tmp/out.json
aws s3 ls s3://data-ingestion-lambda-to-s3-us-east-1/bitcoin_prices.json
```

## Teardown

```bash
aws lambda delete-function --function-name data-ingestion-to-s3
aws s3 rm s3://data-ingestion-lambda-to-s3-us-east-1/bitcoin_prices.json
rm -f function.zip
```

## Notes

- The CoinDesk endpoint in the source (`api.coindesk.com/v1/bpi/currentprice.json`)
  was retired by the upstream provider in 2024. To verify the deploy path
  works end-to-end, point the function at any public JSON API — e.g.
  `https://api.github.com/repos/aws/aws-cli`.
- The handler does not handle the case where the API returns a non-2xx
  status. For a more production-shaped ingest pattern see
  `05_bigdata/lambda-api-ingestion/`, which adds SQS, Secrets Manager,
  KMS encryption, and partitioned S3 keys.

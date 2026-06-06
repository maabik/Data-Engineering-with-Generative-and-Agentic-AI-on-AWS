"""Seed Lambda: writes a small IMDB-style principals dataset to S3.

Creates a CSV file that the Glue crawler can catalog, demonstrating the
column-level and row-level Lake Formation data filters from Chapter 3.

Schema mirrors the book example:
  id, name, category, job, characters

category values: actor, actress, writer, director
"""

import csv
import io
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ["BUCKET_NAME"]
KEY = "imdb/principals/principals.csv"

ROWS = [
    ("tt0000001", "nm0005690", "actor",    "self",           '["Herself"]'),
    ("tt0000001", "nm0374658", "director", None,             "\\N"),
    ("tt0000002", "nm0721526", "director", None,             "\\N"),
    ("tt0000003", "nm0721526", "director", None,             "\\N"),
    ("tt0000003", "nm0846797", "actress",  "self",           '["Herself"]'),
    ("tt0000004", "nm0005690", "actor",    "self",           '["Himself"]'),
    ("tt0000005", "nm0005690", "actor",    "self",           '["Himself"]'),
    ("tt0000005", "nm0005690", "actress",  "self",           '["Herself"]'),
    ("tt0000005", "nm0721526", "director", None,             "\\N"),
    ("tt0000006", "nm0005690", "actor",    "self",           '["Himself"]'),
    ("tt0000007", "nm0005690", "actor",    "self",           '["Himself"]'),
    ("tt0000007", "nm0042589", "writer",   "screenplay",     "\\N"),
    ("tt0000007", "nm0236628", "writer",   "story",          "\\N"),
    ("tt0000008", "nm0005690", "actress",  "self",           '["Herself"]'),
    ("tt0000008", "nm0183947", "director", None,             "\\N"),
    ("tt0000009", "nm0183947", "actress",  "self",           '["Miss Willful"]'),
    ("tt0000009", "nm0183947", "director", None,             "\\N"),
    ("tt0000010", "nm0525910", "actor",    "self",           '["Himself"]'),
    ("tt0000010", "nm0525910", "writer",   "story",          "\\N"),
    ("tt0000011", "nm0804434", "actor",    "self",           '["Himself"]'),
    ("tt0000012", "nm0525910", "actor",    "self",           '["Himself"]'),
    ("tt0000012", "nm0525910", "director", None,             "\\N"),
    ("tt0000013", "nm0525910", "actor",    "self",           '["Himself"]'),
    ("tt0000014", "nm0525910", "actress",  "self",           '["Herself"]'),
    ("tt0000015", "nm0525910", "actor",    "self",           '["Himself"]'),
]

s3 = boto3.client("s3")


def lambda_handler(event, context):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["tconst", "nconst", "category", "job", "characters"])
    writer.writerows(ROWS)

    s3.put_object(
        Bucket=BUCKET,
        Key=KEY,
        Body=buf.getvalue().encode(),
        ContentType="text/csv",
    )
    logger.info("Seeded %d rows to s3://%s/%s", len(ROWS), BUCKET, KEY)
    return {"statusCode": 200, "rows": len(ROWS), "s3_key": KEY}

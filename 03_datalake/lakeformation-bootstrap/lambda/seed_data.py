"""Seed Lambda: writes a sample Chicago-style ordinance violations CSV to S3.

The dataset mimics the Chicago Building Ordinance Violations table referenced
in Chapter 4 of the book and gives us columns suited to fine-grained access
control demos:

  - personal_email, owner_phone     -> good targets for PII column hiding
  - violation_district              -> good target for row filtering / LF-TBAC
  - imposed_fine                    -> good target for numeric row filtering

The CSV is intentionally tiny (a few dozen rows) so the demo is fast and free.
"""

import csv
import io
import os

import boto3

BUCKET_NAME = os.environ["BUCKET_NAME"]
S3_KEY = "chicago_violations/violations.csv"

ROWS = [
    # id, docket, address, district, category, imposed_fine, owner_email, owner_phone, violation_date
    ("V0001", "DOCK-1001", "123 N State St",     "CENTRAL",   "BUILDING",   2500, "alice@acme.com",   "312-555-0101", "2024-03-12"),
    ("V0002", "DOCK-1002", "456 W Madison St",   "CENTRAL",   "ELECTRICAL", 750,  "bob@acme.com",     "312-555-0102", "2024-03-15"),
    ("V0003", "DOCK-1003", "789 S Halsted St",   "NEAR_WEST", "PLUMBING",   1200, "carol@acme.com",   "312-555-0103", "2024-03-18"),
    ("V0004", "DOCK-1004", "321 W Diversey",     "NORTH",     "BUILDING",   3000, "dave@acme.com",    "773-555-0104", "2024-03-20"),
    ("V0005", "DOCK-1005", "654 E 47th St",      "SOUTH",     "ELECTRICAL", 500,  "eve@acme.com",     "773-555-0105", "2024-03-22"),
    ("V0006", "DOCK-1006", "987 N Lincoln",      "NORTH",     "BUILDING",   4200, "frank@acme.com",   "773-555-0106", "2024-03-25"),
    ("V0007", "DOCK-1007", "147 W Roosevelt",    "NEAR_WEST", "PLUMBING",   1800, "grace@acme.com",   "312-555-0107", "2024-03-28"),
    ("V0008", "DOCK-1008", "258 S Western",      "WEST",      "BUILDING",   2200, "henry@acme.com",   "773-555-0108", "2024-04-01"),
    ("V0009", "DOCK-1009", "369 N Damen",        "NORTH",     "ZONING",     950,  "iris@acme.com",    "773-555-0109", "2024-04-03"),
    ("V0010", "DOCK-1010", "741 S Stony Island", "SOUTH",     "BUILDING",   5500, "jack@acme.com",    "773-555-0110", "2024-04-05"),
    ("V0011", "DOCK-1011", "852 W Belmont",      "NORTH",     "ELECTRICAL", 600,  "kara@acme.com",    "773-555-0111", "2024-04-08"),
    ("V0012", "DOCK-1012", "963 E 79th St",      "SOUTH",     "PLUMBING",   1100, "leo@acme.com",     "773-555-0112", "2024-04-10"),
    ("V0013", "DOCK-1013", "159 W Lake St",      "CENTRAL",   "BUILDING",   2750, "mia@acme.com",     "312-555-0113", "2024-04-12"),
    ("V0014", "DOCK-1014", "267 N Ashland",      "NEAR_WEST", "ZONING",     1450, "nick@acme.com",    "773-555-0114", "2024-04-15"),
    ("V0015", "DOCK-1015", "378 S Kedzie",       "WEST",      "BUILDING",   3300, "olivia@acme.com",  "773-555-0115", "2024-04-18"),
    ("V0016", "DOCK-1016", "489 N Pulaski",      "NORTH",     "ELECTRICAL", 825,  "peter@acme.com",   "773-555-0116", "2024-04-20"),
    ("V0017", "DOCK-1017", "591 S Cottage Grove","SOUTH",     "BUILDING",   4800, "quinn@acme.com",   "773-555-0117", "2024-04-22"),
    ("V0018", "DOCK-1018", "682 W Fullerton",    "NORTH",     "PLUMBING",   1650, "rachel@acme.com",  "773-555-0118", "2024-04-25"),
    ("V0019", "DOCK-1019", "793 E 87th St",      "SOUTH",     "ZONING",     1050, "sam@acme.com",     "773-555-0119", "2024-04-28"),
    ("V0020", "DOCK-1020", "894 N Milwaukee",    "NEAR_WEST", "BUILDING",   2900, "tina@acme.com",    "773-555-0120", "2024-05-01"),
    ("V0021", "DOCK-1021", "905 W North Ave",    "NORTH",     "ELECTRICAL", 575,  "uma@acme.com",     "773-555-0121", "2024-05-03"),
    ("V0022", "DOCK-1022", "116 S Pulaski",      "WEST",      "BUILDING",   3700, "victor@acme.com",  "773-555-0122", "2024-05-05"),
    ("V0023", "DOCK-1023", "227 N Clark",        "CENTRAL",   "PLUMBING",   1350, "wendy@acme.com",   "312-555-0123", "2024-05-08"),
    ("V0024", "DOCK-1024", "338 S Ashland",      "NEAR_WEST", "ZONING",     1150, "xavier@acme.com",  "773-555-0124", "2024-05-10"),
    ("V0025", "DOCK-1025", "449 W 59th St",      "SOUTH",     "BUILDING",   6200, "yara@acme.com",    "773-555-0125", "2024-05-12"),
]

HEADER = [
    "id", "docket_number", "address", "violation_district", "category",
    "imposed_fine", "owner_email", "owner_phone", "violation_date",
]


def lambda_handler(event, _context):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADER)
    writer.writerows(ROWS)

    boto3.client("s3").put_object(
        Bucket=BUCKET_NAME,
        Key=S3_KEY,
        Body=buf.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )

    return {
        "statusCode": 200,
        "rows": len(ROWS),
        "s3_key": S3_KEY,
    }

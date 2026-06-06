#!/usr/bin/env python3
"""Programmatically create Lake Formation data cells filters with boto3.

Companion to the CDK stack in this project (Chapter 3, p. 124). The CDK
stack provisions the same two filters declaratively via CfnDataCellsFilter;
this script does it imperatively against an existing Glue catalog table —
the form a data engineer reaches for when adding a new persona on top of an
already-deployed lakehouse.

Two filters are created on database `imdb`, table `principals`:

    analysts-restricted
        columns: tconst, nconst, category
        rows:    category = 'actor' OR category = 'actress'

    writers-view
        columns: tconst, nconst, category, job
        rows:    category = 'writer'

Optionally, pass --analyst-role-arn / --writer-role-arn to also grant SELECT
on each filter to a principal (the same wiring CDK does via
CfnPrincipalPermissions).

Prereqs:
    - The Glue table (imdb.principals) already exists (CDK stack deployed,
      or any equivalent catalog registration).
    - Caller has lakeformation:CreateDataCellsFilter and (if granting)
      lakeformation:GrantPermissions.
    - Caller is a Lake Formation Data Lake Administrator, or has been
      granted DESCRIBE on the table.

Usage:
    python create_filters_boto3.py \\
        --database imdb \\
        --table principals \\
        --region us-east-1

    # Idempotent re-run: --replace deletes any existing filter with the
    # same (catalog,db,table,name) before recreating it.
    python create_filters_boto3.py --replace

    # Also grant SELECT on each filter to its persona role:
    python create_filters_boto3.py \\
        --analyst-role-arn arn:aws:iam::111122223333:role/AnalystRole \\
        --writer-role-arn  arn:aws:iam::111122223333:role/WriterResearcherRole
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable

import boto3
from botocore.exceptions import ClientError


FILTERS = [
    {
        "name": "analysts-restricted",
        "column_names": ["tconst", "nconst", "category"],
        "row_filter_expression": "category='actor' OR category='actress'",
    },
    {
        "name": "writers-view",
        "column_names": ["tconst", "nconst", "category", "job"],
        "row_filter_expression": "category='writer'",
    },
]


def create_filter(
    lf,
    *,
    catalog_id: str,
    database: str,
    table: str,
    name: str,
    column_names: list[str],
    row_filter_expression: str,
    replace: bool,
) -> None:
    table_data = {
        "TableCatalogId": catalog_id,
        "DatabaseName": database,
        "TableName": table,
        "Name": name,
        "RowFilter": {"FilterExpression": row_filter_expression},
        "ColumnNames": column_names,
    }

    if replace:
        try:
            lf.delete_data_cells_filter(
                TableCatalogId=catalog_id,
                DatabaseName=database,
                TableName=table,
                Name=name,
            )
            print(f"  deleted existing filter '{name}'")
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityNotFoundException":
                raise

    try:
        lf.create_data_cells_filter(TableData=table_data)
        print(f"  created filter '{name}'")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            print(f"  filter '{name}' already exists — pass --replace to recreate")
        else:
            raise


def grant_select_on_filter(
    lf,
    *,
    catalog_id: str,
    database: str,
    table: str,
    filter_name: str,
    principal_arn: str,
) -> None:
    lf.grant_permissions(
        Principal={"DataLakePrincipalIdentifier": principal_arn},
        Resource={
            "DataCellsFilter": {
                "TableCatalogId": catalog_id,
                "DatabaseName": database,
                "TableName": table,
                "Name": filter_name,
            }
        },
        Permissions=["SELECT"],
        PermissionsWithGrantOption=[],
    )
    print(f"  granted SELECT on '{filter_name}' to {principal_arn}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--database", default="imdb", help="Glue database name (default: imdb)")
    p.add_argument("--table", default="principals", help="Glue table name (default: principals)")
    p.add_argument("--catalog-id", default=None, help="Glue catalog ID (default: caller account)")
    p.add_argument("--region", default=None, help="AWS region (default: from environment)")
    p.add_argument("--replace", action="store_true",
                   help="Delete existing filters with the same name before creating")
    p.add_argument("--analyst-role-arn", default=None,
                   help="If set, grant SELECT on 'analysts-restricted' to this principal")
    p.add_argument("--writer-role-arn", default=None,
                   help="If set, grant SELECT on 'writers-view' to this principal")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    session = boto3.session.Session(region_name=args.region)
    catalog_id = args.catalog_id or session.client("sts").get_caller_identity()["Account"]
    lf = session.client("lakeformation")

    print(f"Creating data cells filters on {catalog_id}:{args.database}.{args.table}")
    for spec in FILTERS:
        create_filter(
            lf,
            catalog_id=catalog_id,
            database=args.database,
            table=args.table,
            name=spec["name"],
            column_names=spec["column_names"],
            row_filter_expression=spec["row_filter_expression"],
            replace=args.replace,
        )

    grants = [
        ("analysts-restricted", args.analyst_role_arn),
        ("writers-view", args.writer_role_arn),
    ]
    for filter_name, principal_arn in grants:
        if principal_arn:
            grant_select_on_filter(
                lf,
                catalog_id=catalog_id,
                database=args.database,
                table=args.table,
                filter_name=filter_name,
                principal_arn=principal_arn,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

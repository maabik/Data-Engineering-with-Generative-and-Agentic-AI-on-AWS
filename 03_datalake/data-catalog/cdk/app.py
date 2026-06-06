#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.data_catalog_stack import DataCatalogStack

app = cdk.App()
DataCatalogStack(
    app, "DataCatalog",
    description="Bitcoin prices data catalog: time-partitioned ingestion, Glue crawler, Bedrock schema annotation, drift alerting",
)
app.synth()

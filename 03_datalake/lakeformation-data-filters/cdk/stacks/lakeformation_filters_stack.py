"""CDK stack: Lake Formation Data Filters

Demonstrates Chapter 3 fine-grained access control via Lake Formation data filters:

  - Column-level security: restrict principals to a subset of columns
  - Row-level security:    PartiQL expressions that filter rows by column value

Architecture:
  - S3 bucket — stores the IMDB principals CSV dataset
  - Glue database (imdb) + crawler — catalogs the table
  - Lake Formation S3 registration
  - Two data filters on the principals table:
      * analysts-restricted  — columns: tconst, nconst, category
                               rows:    category = 'actor' OR category = 'actress'
      * writers-view         — columns: tconst, nconst, category, job
                               rows:    category = 'writer'
  - Two IAM roles simulating analyst and writer principals
  - Lake Formation grants associating each role with its data filter
  - Seed Lambda — writes sample CSV data to S3
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_glue as glue,
    aws_iam as iam,
    aws_lakeformation as lakeformation,
    aws_lambda as lambda_,
    aws_s3 as s3,
)
from constructs import Construct

DATABASE_NAME = "imdb"
TABLE_NAME = "principals"


class LakeFormationFiltersStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── S3 data bucket ────────────────────────────────────────────────────
        data_bucket = s3.Bucket(
            self, "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ── Glue database ─────────────────────────────────────────────────────
        glue_db = glue.CfnDatabase(
            self, "ImdbDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=DATABASE_NAME,
                description="IMDB principals dataset — Lake Formation data filter demo",
            ),
        )

        # ── Glue crawler role ─────────────────────────────────────────────────
        crawler_role = iam.Role(
            self, "CrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
            ],
        )
        data_bucket.grant_read(crawler_role)

        # ── Glue table (pre-defined so data filters can reference it at deploy) ─
        # CfnDataCellsFilter requires the table to exist before it can be created.
        # We define the schema here; the crawler will merge any schema updates later.
        glue_table = glue.CfnTable(
            self, "PrincipalsTable",
            catalog_id=self.account,
            database_name=DATABASE_NAME,
            table_input=glue.CfnTable.TableInputProperty(
                name=TABLE_NAME,
                description="IMDB principals — tconst, nconst, category, job, characters",
                table_type="EXTERNAL_TABLE",
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=data_bucket.s3_url_for_object("imdb/principals/"),
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.hadoop.hive.serde2.OpenCSVSerde",
                        parameters={
                            "separatorChar": ",",
                            "quoteChar": '"',
                            "escapeChar": "\\",
                        },
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="tconst",     type="string"),
                        glue.CfnTable.ColumnProperty(name="nconst",     type="string"),
                        glue.CfnTable.ColumnProperty(name="category",   type="string"),
                        glue.CfnTable.ColumnProperty(name="job",        type="string"),
                        glue.CfnTable.ColumnProperty(name="characters", type="string"),
                    ],
                ),
                parameters={"skip.header.line.count": "1"},
            ),
        )
        glue_table.add_dependency(glue_db)

        # ── Glue crawler ──────────────────────────────────────────────────────
        glue.CfnCrawler(
            self, "PrincipalsCrawler",
            name="imdb-principals-crawler",
            role=crawler_role.role_arn,
            database_name=DATABASE_NAME,
            description="Crawler for IMDB principals CSV",
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=data_bucket.s3_url_for_object("imdb/principals/"),
                    )
                ]
            ),
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="LOG",
                delete_behavior="LOG",
            ),
            recrawl_policy=glue.CfnCrawler.RecrawlPolicyProperty(
                recrawl_behavior="CRAWL_NEW_FOLDERS_ONLY",
            ),
        ).add_dependency(glue_db)

        # ── Lake Formation: register S3 location ─────────────────────────────
        lakeformation.CfnResource(
            self, "LFDataBucket",
            resource_arn=data_bucket.bucket_arn,
            use_service_linked_role=True,
        )

        # Grant crawler role LF permissions to create/update tables
        lakeformation.CfnPrincipalPermissions(
            self, "CrawlerLFDbPermission",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=crawler_role.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                database=lakeformation.CfnPrincipalPermissions.DatabaseResourceProperty(
                    catalog_id=self.account,
                    name=DATABASE_NAME,
                ),
            ),
            permissions=["CREATE_TABLE", "DESCRIBE", "ALTER"],
            permissions_with_grant_option=[],
        ).add_dependency(glue_db)

        lakeformation.CfnPrincipalPermissions(
            self, "CrawlerLFTablePermission",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=crawler_role.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                table=lakeformation.CfnPrincipalPermissions.TableResourceProperty(
                    catalog_id=self.account,
                    database_name=DATABASE_NAME,
                    table_wildcard={},
                ),
            ),
            permissions=["ALL"],
            permissions_with_grant_option=[],
        ).add_dependency(glue_db)

        # ── Data filter 1: analysts-restricted ───────────────────────────────
        # Column-level: tconst, nconst, category only (excludes job, characters)
        # Row-level:    actors and actresses only (PartiQL expression)
        analysts_filter = lakeformation.CfnDataCellsFilter(
            self, "AnalystsFilter",
            name="analysts-restricted",
            database_name=DATABASE_NAME,
            table_name=TABLE_NAME,
            table_catalog_id=self.account,
            column_names=["tconst", "nconst", "category"],
            row_filter=lakeformation.CfnDataCellsFilter.RowFilterProperty(
                filter_expression="category='actor' OR category='actress'",
            ),
        )
        analysts_filter.add_dependency(glue_table)

        # ── Data filter 2: writers-view ───────────────────────────────────────
        # Column-level: tconst, nconst, category, job (excludes characters)
        # Row-level:    writers only
        writers_filter = lakeformation.CfnDataCellsFilter(
            self, "WritersFilter",
            name="writers-view",
            database_name=DATABASE_NAME,
            table_name=TABLE_NAME,
            table_catalog_id=self.account,
            column_names=["tconst", "nconst", "category", "job"],
            row_filter=lakeformation.CfnDataCellsFilter.RowFilterProperty(
                filter_expression="category='writer'",
            ),
        )
        writers_filter.add_dependency(glue_table)

        # ── Simulated analyst IAM role ────────────────────────────────────────
        analyst_role = iam.Role(
            self, "AnalystRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Simulated analyst - sees actors/actresses, 3 columns only",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAthenaFullAccess"),
            ],
        )
        data_bucket.grant_read(analyst_role)

        # ── Simulated writer-researcher IAM role ──────────────────────────────
        writer_role = iam.Role(
            self, "WriterResearcherRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Simulated writer researcher - sees writers only, 4 columns",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAthenaFullAccess"),
            ],
        )
        data_bucket.grant_read(writer_role)

        # ── LF grant: analyst role ← analysts-restricted filter ───────────────
        lakeformation.CfnPrincipalPermissions(
            self, "AnalystTableGrant",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=analyst_role.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                data_cells_filter=lakeformation.CfnPrincipalPermissions.DataCellsFilterResourceProperty(
                    database_name=DATABASE_NAME,
                    name="analysts-restricted",
                    table_catalog_id=self.account,
                    table_name=TABLE_NAME,
                ),
            ),
            permissions=["SELECT"],
            permissions_with_grant_option=[],
        ).add_dependency(analysts_filter)

        # ── LF grant: writer role ← writers-view filter ───────────────────────
        lakeformation.CfnPrincipalPermissions(
            self, "WriterTableGrant",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=writer_role.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                data_cells_filter=lakeformation.CfnPrincipalPermissions.DataCellsFilterResourceProperty(
                    database_name=DATABASE_NAME,
                    name="writers-view",
                    table_catalog_id=self.account,
                    table_name=TABLE_NAME,
                ),
            ),
            permissions=["SELECT"],
            permissions_with_grant_option=[],
        ).add_dependency(writers_filter)

        # ── Seed Lambda role ──────────────────────────────────────────────────
        seed_role = iam.Role(
            self, "SeedLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        data_bucket.grant_put(seed_role)

        # ── Seed Lambda ───────────────────────────────────────────────────────
        seed_fn = lambda_.Function(
            self, "SeedFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="seed_imdb.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=seed_role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={"BUCKET_NAME": data_bucket.bucket_name},
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        cdk.CfnOutput(self, "GlueDatabaseName", value=DATABASE_NAME)
        cdk.CfnOutput(self, "CrawlerName", value="imdb-principals-crawler")
        cdk.CfnOutput(self, "SeedFunctionName", value=seed_fn.function_name)
        cdk.CfnOutput(self, "AnalystRoleArn", value=analyst_role.role_arn,
                      description="Assume this role to query with analysts-restricted filter")
        cdk.CfnOutput(self, "WriterRoleArn", value=writer_role.role_arn,
                      description="Assume this role to query with writers-view filter")

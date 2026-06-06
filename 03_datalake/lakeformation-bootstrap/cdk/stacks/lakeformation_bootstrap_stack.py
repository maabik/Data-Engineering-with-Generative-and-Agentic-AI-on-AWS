"""CDK stack: Lake Formation Bootstrap.

End-to-end Lake Formation data lake with the four 'fancy' fine-grained controls
referenced in Chapter 3 of the book:

  1. LF-Tag-based access control (LF-TBAC)
       - Defines two LF-Tags: classification (public|internal|confidential)
                              department    (finance|operations|public_safety)
       - Tags assigned to the database, table, and selected columns
       - One IAM role granted SELECT via a tag expression rather than a
         direct table grant

  2. Column-level filter (data filter, column allowlist)
       - 'analyst-no-pii' filter excludes owner_email and owner_phone

  3. Row-level filter (data filter, row predicate)
       - 'south-district-only' filter restricts rows to violation_district='SOUTH'

  4. Cross-account share (optional, parameterized)
       - If `consumerAccountId` context is set, the database is shared with
         that AWS account via Lake Formation + RAM.

Architecture:

  S3 bucket --(LF data location registration)--> Lake Formation
        |
        +-> Glue database 'chicago_violations'
              +-> Glue table 'violations' (CSV, 9 columns, 25 rows)

  Roles:
    DataLakeAdminRole  -> LF admin (manages tags, filters, grants)
    TaggedAnalystRole  -> SELECT via LF-Tag expression (LF-TBAC demo)
    NoPiiAnalystRole   -> SELECT via column-level filter (analyst-no-pii)
    SouthDistrictRole  -> SELECT via row-level filter (south-district-only)

  Seed:
    SeedFunction Lambda -> writes 25-row CSV when invoked
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

DATABASE_NAME = "chicago_violations"
TABLE_NAME = "violations"
DATA_PREFIX = "chicago_violations/"

# LF-Tags
TAG_CLASSIFICATION = "classification"
TAG_DEPARTMENT = "department"


class LakeFormationBootstrapStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        consumer_account_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── S3 data lake bucket ───────────────────────────────────────────────
        data_bucket = s3.Bucket(
            self, "DataLakeBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=False,
        )

        # ── Register S3 location with Lake Formation ──────────────────────────
        # NOTE: this stack does NOT manage LF data lake admins (CfnDataLakeSettings
        # would overwrite the entire admin list). The deployer is expected to
        # already have LF admin status. The CDK CFN execution role must also be
        # an LF admin — see README prerequisites.
        lf_location = lakeformation.CfnResource(
            self, "LFDataLocation",
            resource_arn=data_bucket.bucket_arn,
            use_service_linked_role=True,
        )

        # ── Glue database ─────────────────────────────────────────────────────
        glue_db = glue.CfnDatabase(
            self, "ViolationsDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=DATABASE_NAME,
                description="Chicago ordinance violations - LF bootstrap demo",
            ),
        )

        # ── Glue table ────────────────────────────────────────────────────────
        # Pre-defined so LF data filters and column tags can target columns
        # at deploy time. The schema mirrors the CSV emitted by the seed Lambda.
        glue_table = glue.CfnTable(
            self, "ViolationsTable",
            catalog_id=self.account,
            database_name=DATABASE_NAME,
            table_input=glue.CfnTable.TableInputProperty(
                name=TABLE_NAME,
                description="Chicago ordinance violations sample dataset",
                table_type="EXTERNAL_TABLE",
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=data_bucket.s3_url_for_object(DATA_PREFIX),
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
                        glue.CfnTable.ColumnProperty(name="id",                  type="string"),
                        glue.CfnTable.ColumnProperty(name="docket_number",       type="string"),
                        glue.CfnTable.ColumnProperty(name="address",             type="string"),
                        glue.CfnTable.ColumnProperty(name="violation_district",  type="string"),
                        glue.CfnTable.ColumnProperty(name="category",            type="string"),
                        glue.CfnTable.ColumnProperty(name="imposed_fine",        type="int"),
                        glue.CfnTable.ColumnProperty(name="owner_email",         type="string"),
                        glue.CfnTable.ColumnProperty(name="owner_phone",         type="string"),
                        glue.CfnTable.ColumnProperty(name="violation_date",      type="string"),
                    ],
                ),
                parameters={"skip.header.line.count": "1"},
            ),
        )
        glue_table.add_dependency(glue_db)

        # ╔═════════════════════════════════════════════════════════════════════╗
        # ║ FANCY CONTROL #1: LF-Tag-based access control (LF-TBAC)             ║
        # ╚═════════════════════════════════════════════════════════════════════╝

        tag_classification = lakeformation.CfnTag(
            self, "TagClassification",
            catalog_id=self.account,
            tag_key=TAG_CLASSIFICATION,
            tag_values=["public", "internal", "confidential"],
        )

        tag_department = lakeformation.CfnTag(
            self, "TagDepartment",
            catalog_id=self.account,
            tag_key=TAG_DEPARTMENT,
            tag_values=["finance", "operations", "public_safety"],
        )

        # Assign tags to the database (department=public_safety).
        db_tag_assoc = lakeformation.CfnTagAssociation(
            self, "DbTagAssoc",
            lf_tags=[
                lakeformation.CfnTagAssociation.LFTagPairProperty(
                    catalog_id=self.account,
                    tag_key=TAG_DEPARTMENT,
                    tag_values=["public_safety"],
                ),
            ],
            resource=lakeformation.CfnTagAssociation.ResourceProperty(
                database=lakeformation.CfnTagAssociation.DatabaseResourceProperty(
                    catalog_id=self.account,
                    name=DATABASE_NAME,
                ),
            ),
        )
        db_tag_assoc.add_dependency(tag_department)
        db_tag_assoc.add_dependency(glue_db)

        # Assign tags to non-PII columns (classification=internal).
        non_pii_columns = [
            "id", "docket_number", "address", "violation_district",
            "category", "imposed_fine", "violation_date",
        ]
        for col in non_pii_columns:
            assoc = lakeformation.CfnTagAssociation(
                self, f"ColTagInternal_{col}",
                lf_tags=[
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=self.account,
                        tag_key=TAG_CLASSIFICATION,
                        tag_values=["internal"],
                    ),
                ],
                resource=lakeformation.CfnTagAssociation.ResourceProperty(
                    table_with_columns=lakeformation.CfnTagAssociation.TableWithColumnsResourceProperty(
                        catalog_id=self.account,
                        database_name=DATABASE_NAME,
                        name=TABLE_NAME,
                        column_names=[col],
                    ),
                ),
            )
            assoc.add_dependency(tag_classification)
            assoc.add_dependency(glue_table)

        # Assign tags to PII columns (classification=confidential).
        pii_columns = ["owner_email", "owner_phone"]
        for col in pii_columns:
            assoc = lakeformation.CfnTagAssociation(
                self, f"ColTagConfidential_{col}",
                lf_tags=[
                    lakeformation.CfnTagAssociation.LFTagPairProperty(
                        catalog_id=self.account,
                        tag_key=TAG_CLASSIFICATION,
                        tag_values=["confidential"],
                    ),
                ],
                resource=lakeformation.CfnTagAssociation.ResourceProperty(
                    table_with_columns=lakeformation.CfnTagAssociation.TableWithColumnsResourceProperty(
                        catalog_id=self.account,
                        database_name=DATABASE_NAME,
                        name=TABLE_NAME,
                        column_names=[col],
                    ),
                ),
            )
            assoc.add_dependency(tag_classification)
            assoc.add_dependency(glue_table)

        # Tagged analyst — SELECT on any table tagged classification=internal
        # AND department=public_safety. PII columns (confidential) are
        # excluded automatically because LF evaluates tags per-column.
        tagged_analyst = iam.Role(
            self, "TaggedAnalystRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="LF-TBAC analyst - sees internal+public_safety only",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAthenaFullAccess"),
            ],
        )
        data_bucket.grant_read(tagged_analyst)

        tagged_grant = lakeformation.CfnPrincipalPermissions(
            self, "TaggedAnalystTagGrant",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=tagged_analyst.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                lf_tag_policy=lakeformation.CfnPrincipalPermissions.LFTagPolicyResourceProperty(
                    catalog_id=self.account,
                    resource_type="TABLE",
                    expression=[
                        lakeformation.CfnPrincipalPermissions.LFTagProperty(
                            tag_key=TAG_CLASSIFICATION,
                            tag_values=["internal"],
                        ),
                        lakeformation.CfnPrincipalPermissions.LFTagProperty(
                            tag_key=TAG_DEPARTMENT,
                            tag_values=["public_safety"],
                        ),
                    ],
                ),
            ),
            permissions=["SELECT", "DESCRIBE"],
            permissions_with_grant_option=[],
        )
        tagged_grant.add_dependency(db_tag_assoc)

        # Tagged analyst also needs DESCRIBE on the database to find tables.
        lakeformation.CfnPrincipalPermissions(
            self, "TaggedAnalystDbDescribe",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=tagged_analyst.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                database=lakeformation.CfnPrincipalPermissions.DatabaseResourceProperty(
                    catalog_id=self.account,
                    name=DATABASE_NAME,
                ),
            ),
            permissions=["DESCRIBE"],
            permissions_with_grant_option=[],
        ).add_dependency(glue_db)

        # ╔═════════════════════════════════════════════════════════════════════╗
        # ║ FANCY CONTROL #2: Column-level data filter                          ║
        # ╚═════════════════════════════════════════════════════════════════════╝

        no_pii_filter = lakeformation.CfnDataCellsFilter(
            self, "NoPiiFilter",
            name="analyst-no-pii",
            database_name=DATABASE_NAME,
            table_name=TABLE_NAME,
            table_catalog_id=self.account,
            column_names=[
                "id", "docket_number", "address", "violation_district",
                "category", "imposed_fine", "violation_date",
            ],
            row_filter=lakeformation.CfnDataCellsFilter.RowFilterProperty(
                all_rows_wildcard={},
            ),
        )
        no_pii_filter.add_dependency(glue_table)

        no_pii_analyst = iam.Role(
            self, "NoPiiAnalystRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Analyst - sees all rows, PII columns excluded",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAthenaFullAccess"),
            ],
        )
        data_bucket.grant_read(no_pii_analyst)

        lakeformation.CfnPrincipalPermissions(
            self, "NoPiiAnalystGrant",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=no_pii_analyst.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                data_cells_filter=lakeformation.CfnPrincipalPermissions.DataCellsFilterResourceProperty(
                    database_name=DATABASE_NAME,
                    name="analyst-no-pii",
                    table_catalog_id=self.account,
                    table_name=TABLE_NAME,
                ),
            ),
            permissions=["SELECT"],
            permissions_with_grant_option=[],
        ).add_dependency(no_pii_filter)

        # ╔═════════════════════════════════════════════════════════════════════╗
        # ║ FANCY CONTROL #3: Row-level data filter                             ║
        # ╚═════════════════════════════════════════════════════════════════════╝

        south_filter = lakeformation.CfnDataCellsFilter(
            self, "SouthDistrictFilter",
            name="south-district-only",
            database_name=DATABASE_NAME,
            table_name=TABLE_NAME,
            table_catalog_id=self.account,
            column_names=[
                "id", "docket_number", "address", "violation_district",
                "category", "imposed_fine", "violation_date",
            ],
            row_filter=lakeformation.CfnDataCellsFilter.RowFilterProperty(
                filter_expression="violation_district='SOUTH'",
            ),
        )
        south_filter.add_dependency(glue_table)

        south_role = iam.Role(
            self, "SouthDistrictRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Regional analyst - south district rows only, no PII",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonAthenaFullAccess"),
            ],
        )
        data_bucket.grant_read(south_role)

        lakeformation.CfnPrincipalPermissions(
            self, "SouthDistrictGrant",
            principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                data_lake_principal_identifier=south_role.role_arn,
            ),
            resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                data_cells_filter=lakeformation.CfnPrincipalPermissions.DataCellsFilterResourceProperty(
                    database_name=DATABASE_NAME,
                    name="south-district-only",
                    table_catalog_id=self.account,
                    table_name=TABLE_NAME,
                ),
            ),
            permissions=["SELECT"],
            permissions_with_grant_option=[],
        ).add_dependency(south_filter)

        # ╔═════════════════════════════════════════════════════════════════════╗
        # ║ FANCY CONTROL #4: Cross-account share (optional, parameterized)     ║
        # ╚═════════════════════════════════════════════════════════════════════╝

        if consumer_account_id:
            # Direct DESCRIBE+SELECT grant to the external account on the
            # whole database. LF + RAM handle the cross-account plumbing.
            lakeformation.CfnPrincipalPermissions(
                self, "ConsumerAccountDbGrant",
                principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                    data_lake_principal_identifier=consumer_account_id,
                ),
                resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                    database=lakeformation.CfnPrincipalPermissions.DatabaseResourceProperty(
                        catalog_id=self.account,
                        name=DATABASE_NAME,
                    ),
                ),
                permissions=["DESCRIBE"],
                permissions_with_grant_option=["DESCRIBE"],
            ).add_dependency(glue_db)

            lakeformation.CfnPrincipalPermissions(
                self, "ConsumerAccountTableGrant",
                principal=lakeformation.CfnPrincipalPermissions.DataLakePrincipalProperty(
                    data_lake_principal_identifier=consumer_account_id,
                ),
                resource=lakeformation.CfnPrincipalPermissions.ResourceProperty(
                    table=lakeformation.CfnPrincipalPermissions.TableResourceProperty(
                        catalog_id=self.account,
                        database_name=DATABASE_NAME,
                        name=TABLE_NAME,
                    ),
                ),
                permissions=["SELECT", "DESCRIBE"],
                permissions_with_grant_option=["SELECT", "DESCRIBE"],
            ).add_dependency(glue_table)

            cdk.CfnOutput(
                self, "ConsumerAccountId",
                value=consumer_account_id,
                description="Account that received the cross-account share",
            )

        # ── Seed Lambda ───────────────────────────────────────────────────────
        seed_role = iam.Role(
            self, "SeedLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        data_bucket.grant_put(seed_role)

        seed_fn = lambda_.Function(
            self, "SeedFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="seed_data.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            role=seed_role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={"BUCKET_NAME": data_bucket.bucket_name},
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DataBucketName",       value=data_bucket.bucket_name)
        cdk.CfnOutput(self, "GlueDatabaseName",     value=DATABASE_NAME)
        cdk.CfnOutput(self, "GlueTableName",        value=TABLE_NAME)
        cdk.CfnOutput(self, "SeedFunctionName",     value=seed_fn.function_name)
        cdk.CfnOutput(self, "TaggedAnalystRoleArn", value=tagged_analyst.role_arn,
                      description="Assume to query via LF-TBAC")
        cdk.CfnOutput(self, "NoPiiAnalystRoleArn",  value=no_pii_analyst.role_arn,
                      description="Assume to query without PII columns")
        cdk.CfnOutput(self, "SouthDistrictRoleArn", value=south_role.role_arn,
                      description="Assume to query south-district rows only")

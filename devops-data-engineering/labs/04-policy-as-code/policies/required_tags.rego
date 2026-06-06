package main

# Every billable resource must carry these tags. The Owner tag is what
# the budget alarm and auto-teardown Lambda key off of.

required := {"Project", "Environment", "Owner", "ManagedBy"}

billable := {
  "aws_s3_bucket",
  "aws_glue_job",
  "aws_glue_catalog_database",
  "aws_sfn_state_machine",
  "aws_redshift_cluster",
  "aws_mwaa_environment",
  "aws_lambda_function",
}

deny[msg] {
  rc := input.resource_changes[_]
  billable[rc.type]
  rc.change.actions[_] == "create"
  tags := object.get(rc.change.after, "tags_all", {})
  missing := required - {k | tags[k]}
  count(missing) > 0
  msg := sprintf("%q missing tags: %v", [rc.address, missing])
}

package main

# S3 buckets in the curated zone (name contains "-curated-") must have a
# KMS-managed encryption configuration in the same plan. AES256 is allowed
# in sandbox to keep costs down; prod must be aws:kms.

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_s3_bucket"
  rc.change.actions[_] == "create"
  contains(rc.change.after.bucket, "-curated-")
  not has_kms_encryption(rc.address, rc.change.after.bucket)
  is_prod_plan
  msg := sprintf("Curated bucket %q in prod must have aws:kms encryption", [rc.address])
}

is_prod_plan {
  input.variables.environment.value == "prod"
}

has_kms_encryption(_, bucket_name) {
  enc := input.resource_changes[_]
  enc.type == "aws_s3_bucket_server_side_encryption_configuration"
  enc.change.after.rule[_].apply_server_side_encryption_by_default[_].sse_algorithm == "aws:kms"
  enc.change.after.bucket == bucket_name
}

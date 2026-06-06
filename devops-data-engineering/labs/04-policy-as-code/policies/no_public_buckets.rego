package main

# Deny S3 buckets without a public-access block, or with public ACLs.
# Evaluated against `terraform show -json plan.tfplan`.

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_s3_bucket"
  rc.change.actions[_] == "create"
  bucket := rc.change.after
  bucket.acl == "public-read"
  msg := sprintf("S3 bucket %q has acl=public-read", [rc.address])
}

deny[msg] {
  rc := input.resource_changes[_]
  rc.type == "aws_s3_bucket"
  rc.change.actions[_] == "create"

  # Require a corresponding public access block in the same plan.
  not has_public_access_block(rc.address)
  msg := sprintf("S3 bucket %q has no aws_s3_bucket_public_access_block in plan", [rc.address])
}

has_public_access_block(bucket_addr) {
  pab := input.resource_changes[_]
  pab.type == "aws_s3_bucket_public_access_block"
  pab.change.after.block_public_acls == true
  pab.change.after.block_public_policy == true
  pab.change.after.ignore_public_acls == true
  pab.change.after.restrict_public_buckets == true
}

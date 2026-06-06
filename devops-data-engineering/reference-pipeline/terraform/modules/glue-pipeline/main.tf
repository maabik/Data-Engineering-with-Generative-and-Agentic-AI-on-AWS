# Buckets, catalog, IAM, and three Glue jobs (ingest, curate, DQ).
#
# Resources are tagged with var.owner so the lab 3 budget alarm and
# auto-teardown Lambda can find them. Don't strip the owner tag.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

data "aws_caller_identity" "current" {}

locals {
  raw_bucket_name     = "${var.name_prefix}-raw-${data.aws_caller_identity.current.account_id}"
  curated_bucket_name = "${var.name_prefix}-curated-${data.aws_caller_identity.current.account_id}"
  scripts_bucket_name = "${var.name_prefix}-glue-scripts-${data.aws_caller_identity.current.account_id}"
}

# --- Buckets -----------------------------------------------------------------

resource "aws_s3_bucket" "raw" {
  bucket = local.raw_bucket_name
  tags   = { Owner = var.owner }
}

resource "aws_s3_bucket" "curated" {
  bucket = local.curated_bucket_name
  tags   = { Owner = var.owner }
}

resource "aws_s3_bucket" "scripts" {
  bucket = local.scripts_bucket_name
  tags   = { Owner = var.owner }
}

resource "aws_s3_bucket_public_access_block" "all" {
  for_each = {
    raw     = aws_s3_bucket.raw.id
    curated = aws_s3_bucket.curated.id
    scripts = aws_s3_bucket.scripts.id
  }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "all" {
  for_each = {
    raw     = aws_s3_bucket.raw.id
    curated = aws_s3_bucket.curated.id
    scripts = aws_s3_bucket.scripts.id
  }
  bucket = each.value
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    id     = "expire-raw"
    status = "Enabled"
    filter {}

    expiration {
      days = var.raw_retention
    }
  }
}

# --- Glue catalog ------------------------------------------------------------

resource "aws_glue_catalog_database" "raw" {
  name = "${var.name_prefix}_raw"
}

resource "aws_glue_catalog_database" "curated" {
  name = "${var.name_prefix}_curated"
}

# --- IAM ---------------------------------------------------------------------

data "aws_iam_policy_document" "glue_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${var.name_prefix}-glue-pipeline"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
  tags               = { Owner = var.owner }
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_inline" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*",
      aws_s3_bucket.curated.arn, "${aws_s3_bucket.curated.arn}/*",
      aws_s3_bucket.scripts.arn, "${aws_s3_bucket.scripts.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "glue_inline" {
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue_inline.json
}

# --- Glue job script upload --------------------------------------------------

resource "aws_s3_object" "ingest_script" {
  bucket = aws_s3_bucket.scripts.id
  key    = "ingest_orders.py"
  source = "${path.module}/../../../glue_jobs/ingest_orders.py"
  etag   = filemd5("${path.module}/../../../glue_jobs/ingest_orders.py")
}

resource "aws_s3_object" "curate_script" {
  bucket = aws_s3_bucket.scripts.id
  key    = "curate_orders.py"
  source = "${path.module}/../../../glue_jobs/curate_orders.py"
  etag   = filemd5("${path.module}/../../../glue_jobs/curate_orders.py")
}

resource "aws_s3_object" "dq_script" {
  bucket = aws_s3_bucket.scripts.id
  key    = "dq_orders.py"
  source = "${path.module}/../../../glue_jobs/dq_orders.py"
  etag   = filemd5("${path.module}/../../../glue_jobs/dq_orders.py")
}

# --- Glue jobs ---------------------------------------------------------------

resource "aws_glue_job" "ingest" {
  name              = "${var.name_prefix}-ingest-orders"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "4.0"
  number_of_workers = var.glue_workers
  worker_type       = "G.1X"

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.scripts.id}/ingest_orders.py"
    python_version  = "3"
  }

  default_arguments = {
    "--raw_bucket"     = aws_s3_bucket.raw.id
    "--enable-metrics" = "true"
  }

  tags = { Owner = var.owner }
}

resource "aws_glue_job" "curate" {
  name              = "${var.name_prefix}-curate-orders"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "4.0"
  number_of_workers = var.glue_workers
  worker_type       = "G.1X"

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.scripts.id}/curate_orders.py"
    python_version  = "3"
  }

  default_arguments = {
    "--raw_bucket"      = aws_s3_bucket.raw.id
    "--curated_bucket"  = aws_s3_bucket.curated.id
    "--curated_db"      = aws_glue_catalog_database.curated.name
    "--datalake-formats" = "iceberg"
    "--enable-metrics"  = "true"
  }

  tags = { Owner = var.owner }
}

resource "aws_glue_job" "dq" {
  name              = "${var.name_prefix}-dq-orders"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "4.0"
  number_of_workers = var.glue_workers
  worker_type       = "G.1X"

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.scripts.id}/dq_orders.py"
    python_version  = "3"
  }

  default_arguments = {
    "--curated_bucket" = aws_s3_bucket.curated.id
    "--curated_db"     = aws_glue_catalog_database.curated.name
    "--enable-metrics" = "true"
  }

  tags = { Owner = var.owner }
}

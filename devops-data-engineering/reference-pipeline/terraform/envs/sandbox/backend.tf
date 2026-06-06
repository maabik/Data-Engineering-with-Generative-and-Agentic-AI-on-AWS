# Sandbox backend.
#
# Sandbox state lives in a per-account S3 bucket so multiple developers can
# work in parallel; workspaces (TF_WORKSPACE=$USER-feature-x) keep their
# state files separate inside the same bucket.
#
# Bootstrap the bucket + DynamoDB lock table once with
# scripts/bootstrap-tf-backend.sh before running `terraform init` here.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  backend "s3" {
    bucket         = "REPLACE-WITH-YOUR-TF-STATE-BUCKET"
    key            = "devops-data-engineering/reference-pipeline/sandbox.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE-WITH-YOUR-TF-LOCK-TABLE"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "devops-data-engineering"
      Environment = "sandbox"
      Workspace   = terraform.workspace
      Owner       = var.owner
      ManagedBy   = "terraform"
    }
  }
}

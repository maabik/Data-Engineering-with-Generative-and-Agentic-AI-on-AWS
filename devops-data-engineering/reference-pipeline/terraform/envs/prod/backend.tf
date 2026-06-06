terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }

  backend "s3" {
    bucket         = "REPLACE-WITH-YOUR-PROD-TF-STATE-BUCKET"
    key            = "devops-data-engineering/reference-pipeline/prod.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE-WITH-YOUR-PROD-TF-LOCK-TABLE"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "devops-data-engineering"
      Environment = "prod"
      ManagedBy   = "terraform"
    }
  }
}

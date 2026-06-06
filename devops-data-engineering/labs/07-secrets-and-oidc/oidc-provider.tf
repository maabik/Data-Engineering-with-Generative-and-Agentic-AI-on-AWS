# GitHub OIDC provider in AWS. ONE per AWS account.
#
# After applying, the provider ARN is referenced by the role trust policy
# in deploy-role.tf.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

provider "aws" { region = "us-east-1" }

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # GitHub publishes its current thumbprints at
  # https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}

# Per-environment deploy role assumed by GitHub Actions via OIDC.
# Apply once per environment (sandbox/staging/prod), each in its own
# AWS account, with environment-appropriate var values.

variable "environment_name" {
  type        = string
  description = "sandbox | staging | prod"
}

variable "github_repo" {
  type        = string
  description = "Format: org/repo"
}

variable "ref_pattern" {
  type        = string
  description = "Trust policy ref pattern. e.g. 'refs/heads/main' for staging, 'refs/tags/v*' for prod, '*' for sandbox."
  default     = "*"
}

data "aws_caller_identity" "current" {}

locals {
  oidc_provider_arn = aws_iam_openid_connect_provider.github.arn
  role_name         = "${var.environment_name}-deploy-role"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:${var.ref_pattern}"]
    }

    # For prod, also require the matching environment claim. Lab 6's
    # release.yml runs in environment: prod.
    dynamic "condition" {
      for_each = var.environment_name == "prod" ? [1] : []
      content {
        test     = "StringEquals"
        variable = "token.actions.githubusercontent.com:environment"
        values   = ["prod"]
      }
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Sandbox: broad — needs to create everything the reference pipeline does.
# Staging/prod: narrow — only the resource types the deploy actually
# touches. Tighten by adding a customer-managed policy that lists the
# specific resource types and tag conditions.
data "aws_iam_policy_document" "deploy" {
  statement {
    actions = [
      "s3:*",
      "glue:*",
      "states:*",
      "iam:GetRole", "iam:CreateRole", "iam:DeleteRole",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:CreatePolicy", "iam:DeletePolicy",
      "iam:TagRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:GetPolicy", "iam:GetPolicyVersion",
      "cloudwatch:*",
      "logs:*",
      "events:*",
    ]
    resources = ["*"]
  }

  # iam:PassRole is scoped to the roles this deploy role itself creates,
  # via a tag condition the role-creation also writes.
  statement {
    actions   = ["iam:PassRole"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/ManagedBy"
      values   = ["terraform"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

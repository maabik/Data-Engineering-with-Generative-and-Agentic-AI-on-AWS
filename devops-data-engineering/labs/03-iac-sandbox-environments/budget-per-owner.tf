# One AWS Budget per developer / PR sandbox owner. Matches resources by
# the Owner tag set on every billable resource in the reference pipeline.
#
# Usage:
#   terraform init
#   terraform apply -var owner=$USER -var monthly_limit=50

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

provider "aws" { region = "us-east-1" }

variable "owner" {
  type        = string
  description = "Owner tag value to scope the budget to."
}

variable "monthly_limit" {
  type        = number
  default     = 50
  description = "USD monthly budget threshold. Alarm fires at 50/80/100%."
}

variable "alert_emails" {
  type        = list(string)
  default     = []
  description = "Email addresses to notify on threshold breach."
}

resource "aws_budgets_budget" "owner" {
  name         = "sandbox-${var.owner}"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_limit)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Owner$${var.owner}"]
  }

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value < 100 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = var.alert_emails
    }
  }
}

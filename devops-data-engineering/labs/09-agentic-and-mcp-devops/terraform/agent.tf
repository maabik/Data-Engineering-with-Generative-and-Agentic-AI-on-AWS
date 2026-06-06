# Bedrock agent + AgentCore runtime with a guardrail-attachment
# precondition. terraform apply fails fast if no guardrail is configured,
# so "agent in prod without guardrail" can never ship.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

variable "agent_name"      { type = string }
variable "model_id"        { type = string }   # e.g. anthropic.claude-3-5-sonnet-v2
variable "guardrail_id"    { type = string, default = null }
variable "guardrail_version" { type = string, default = "DRAFT" }
variable "system_prompt_path" {
  type    = string
  default = "../prompts/sales_reporter.system.md"
}

locals {
  system_prompt = file(var.system_prompt_path)
}

resource "aws_bedrockagent_agent" "sales_reporter" {
  agent_name              = var.agent_name
  foundation_model        = var.model_id
  instruction             = local.system_prompt
  agent_resource_role_arn = aws_iam_role.agent.arn

  guardrail_configuration {
    guardrail_identifier = var.guardrail_id
    guardrail_version    = var.guardrail_version
  }

  lifecycle {
    precondition {
      condition     = var.guardrail_id != null && var.guardrail_id != ""
      error_message = "Cannot deploy ${var.agent_name} without a Bedrock Guardrail. See 02_security/guardrails-from-policy/."
    }
  }
}

resource "aws_iam_role" "agent" {
  name               = "${var.agent_name}-agent-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
  }
}

# bedrock:InvokeModel is granted via a separate policy that lab 4's
# bedrock_requires_guardrail.rego inspects. Keep them in the same plan.
data "aws_iam_policy_document" "invoke_model" {
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:*::foundation-model/${var.model_id}*"]
  }
  statement {
    actions   = ["bedrock:ApplyGuardrail"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "invoke_model" {
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.invoke_model.json
}

output "agent_id" { value = aws_bedrockagent_agent.sales_reporter.agent_id }

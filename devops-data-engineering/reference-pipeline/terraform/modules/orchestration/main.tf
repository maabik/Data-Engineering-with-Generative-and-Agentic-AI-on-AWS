terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn-role"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn_inline" {
  statement {
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      "glue:BatchStopJobRun",
    ]
    resources = ["*"] # Glue StartJobRun does not support resource-level
  }
  statement {
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn_inline" {
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn_inline.json
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name_prefix}-orders-pipeline"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Reference pipeline: ingest -> curate -> DQ"
    StartAt = "Ingest"
    States = {
      Ingest = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = var.ingestion_job
        }
        Next  = "Curate"
        Retry = [{
          ErrorEquals     = ["States.ALL"]
          IntervalSeconds = 30
          MaxAttempts     = 2
          BackoffRate     = 2.0
        }]
      }
      Curate = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = var.curation_job
        }
        Next = "DataQuality"
      }
      DataQuality = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = var.dq_job
        }
        End = true
      }
    }
  })
}

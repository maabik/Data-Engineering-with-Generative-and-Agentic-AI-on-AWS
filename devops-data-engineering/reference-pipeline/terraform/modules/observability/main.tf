terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/data-pipeline/${var.name_prefix}"
  retention_in_days = 14
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.name_prefix}-orders-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Step Functions execution outcome"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", var.state_machine],
            [".",          "ExecutionsFailed",    ".",               "."],
            [".",          "ExecutionsTimedOut",  ".",               "."],
          ]
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Glue job runtimes"
          stat   = "Average"
          period = 300
          metrics = [
            for job in var.glue_jobs : ["AWS/Glue", "glue.driver.aggregate.elapsedTime", "JobName", job, "JobRunId", "ALL"]
          ]
        }
      },
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${var.name_prefix}-pipeline-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = var.state_machine
  }
}

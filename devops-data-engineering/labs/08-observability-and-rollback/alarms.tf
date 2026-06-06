# Deploy-tied SLO alarm. The metric is emitted from run_dq.py via the
# put_metric() helper in metrics.py. Threshold: any DQ failure on a
# specific deploy fires.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

variable "environment" { type = string }
variable "sns_topic_arn" {
  type        = string
  description = "SNS topic that fans out to PagerDuty / Slack / rollback Lambda."
}

resource "aws_cloudwatch_metric_alarm" "dq_failure" {
  alarm_name          = "${var.environment}-dq-failure"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  metric_name         = "DQFailureCount"
  namespace           = "DataPipeline/Reference"
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]

  alarm_description = "Fires when run_dq.py reports any contract failure for this environment. Triggers rollback via SNS subscriber."
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${var.environment}-pipeline-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.sns_topic_arn]
}

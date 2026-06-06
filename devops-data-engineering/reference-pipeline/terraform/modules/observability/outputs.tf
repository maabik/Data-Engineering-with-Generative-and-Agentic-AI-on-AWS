output "dashboard_name" { value = aws_cloudwatch_dashboard.pipeline.dashboard_name }
output "log_group_name" { value = aws_cloudwatch_log_group.pipeline.name }

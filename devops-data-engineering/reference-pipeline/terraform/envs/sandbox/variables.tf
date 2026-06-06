variable "aws_region" {
  description = "AWS region. Sandboxes default to us-east-1 to match the rest of the book."
  type        = string
  default     = "us-east-1"
}

variable "owner" {
  description = <<EOT
Tag value applied to every billable resource. Required so AWS Cost Explorer,
the lab 3 budget alarms, and the auto-teardown Lambda can attribute spend to
a specific developer or PR. Set TF_VAR_owner=<your-name-or-pr-id>.
EOT
  type        = string

  validation {
    condition     = length(var.owner) > 0
    error_message = "var.owner must be set (TF_VAR_owner=<your-name-or-pr-id>)."
  }
}

variable "name_prefix" {
  description = "Prefix for resource names. Defaults to the workspace name so two parallel sandboxes don't collide."
  type        = string
  default     = ""
}

variable "raw_bucket_retention_days" {
  description = "How long to keep objects in the raw zone before lifecycle expiration. Sandbox is short."
  type        = number
  default     = 7
}

variable "glue_workers" {
  description = "Number of DPU workers for each Glue job. Sandbox stays small."
  type        = number
  default     = 2
}

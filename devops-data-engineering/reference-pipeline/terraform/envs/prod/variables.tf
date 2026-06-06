variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "glue_workers" {
  type    = number
  default = 10
}

variable "raw_bucket_retention_days" {
  type    = number
  default = 90
}

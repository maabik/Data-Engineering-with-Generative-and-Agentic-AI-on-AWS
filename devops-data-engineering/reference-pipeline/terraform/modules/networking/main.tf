# Placeholder networking module. The reference pipeline as-is uses default
# VPC settings — Glue, Step Functions, and Athena reach AWS APIs over public
# endpoints with IAM auth. This module exists so labs 4 (policy-as-code) and
# 8 (observability) have a place to pin VPC endpoints and security groups
# when you tighten the network posture.
#
# Left intentionally minimal so the reference pipeline runs without VPC
# wiring; replace with a full VPC + endpoint setup before promoting to prod.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.50" }
  }
}

variable "name_prefix" {
  type    = string
  default = ""
}

output "note" {
  value = "Reference pipeline runs without a custom VPC. Wire endpoints here when hardening."
}

# Same composition as sandbox/main.tf — environment differences are
# expressed in terraform.tfvars only, NOT by editing modules.

locals {
  prefix = "prod"
}

module "glue_pipeline" {
  source = "../../modules/glue-pipeline"

  name_prefix    = local.prefix
  owner          = "platform-team"
  glue_workers   = var.glue_workers
  raw_retention  = var.raw_bucket_retention_days
}

module "orchestration" {
  source = "../../modules/orchestration"

  name_prefix     = local.prefix
  ingestion_job   = module.glue_pipeline.ingestion_job_name
  curation_job    = module.glue_pipeline.curation_job_name
  dq_job          = module.glue_pipeline.dq_job_name
}

module "observability" {
  source = "../../modules/observability"

  name_prefix     = local.prefix
  state_machine   = module.orchestration.state_machine_arn
  glue_jobs       = [
    module.glue_pipeline.ingestion_job_name,
    module.glue_pipeline.curation_job_name,
    module.glue_pipeline.dq_job_name,
  ]
}

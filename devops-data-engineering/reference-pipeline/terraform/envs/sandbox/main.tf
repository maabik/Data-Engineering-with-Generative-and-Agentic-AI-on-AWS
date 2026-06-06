locals {
  prefix = var.name_prefix != "" ? var.name_prefix : terraform.workspace
}

module "glue_pipeline" {
  source = "../../modules/glue-pipeline"

  name_prefix    = local.prefix
  owner          = var.owner
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

output "raw_bucket"          { value = module.glue_pipeline.raw_bucket }
output "curated_bucket"      { value = module.glue_pipeline.curated_bucket }
output "curated_database"    { value = module.glue_pipeline.curated_database }
output "step_functions_arn"  { value = module.orchestration.state_machine_arn }
output "dashboard_name"      { value = module.observability.dashboard_name }

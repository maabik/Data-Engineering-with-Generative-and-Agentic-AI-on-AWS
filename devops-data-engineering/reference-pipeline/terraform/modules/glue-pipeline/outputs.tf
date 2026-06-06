output "raw_bucket"          { value = aws_s3_bucket.raw.id }
output "curated_bucket"      { value = aws_s3_bucket.curated.id }
output "curated_database"    { value = aws_glue_catalog_database.curated.name }
output "ingestion_job_name"  { value = aws_glue_job.ingest.name }
output "curation_job_name"   { value = aws_glue_job.curate.name }
output "dq_job_name"         { value = aws_glue_job.dq.name }
output "glue_role_arn"       { value = aws_iam_role.glue.arn }

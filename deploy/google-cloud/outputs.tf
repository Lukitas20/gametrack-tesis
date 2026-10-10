output "project_id" {
  value = var.project_id
}
output "region" {
  value = var.region
}
output "connection_name" {
  value = google_sql_database_instance.database.connection_name
}
output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/gametrack"
}
output "build_bucket" {
  value = google_storage_bucket.builds.name
}
output "model_bucket" {
  value = google_storage_bucket.models.name
}
output "migration_bucket" {
  value = google_storage_bucket.migration.name
}
output "public_url" {
  value = var.deploy_web ? local.public_url : null
}

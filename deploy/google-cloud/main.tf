data "google_project" "current" {
  project_id = var.project_id
}

locals {
  apis = toset([
    "run.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com", "storage.googleapis.com", "iam.googleapis.com",
    "logging.googleapis.com", "monitoring.googleapis.com", "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com"
  ])
  identities = toset(["web", "sync", "train", "migrate", "scheduler", "build"])
  db_clients = toset(["web", "sync", "train", "migrate"])
  secrets    = toset(["gametrack-database-url", "gametrack-jwt-key", "gametrack-steam-key"])
  secret_grants = {
    "web-db"     = ["web", "gametrack-database-url"]
    "web-jwt"    = ["web", "gametrack-jwt-key"]
    "web-steam"  = ["web", "gametrack-steam-key"]
    "sync-db"    = ["sync", "gametrack-database-url"]
    "sync-steam" = ["sync", "gametrack-steam-key"]
    "train-db"   = ["train", "gametrack-database-url"]
    "migrate-db" = ["migrate", "gametrack-database-url"]
  }
  base_env = {
    ENVIRONMENT                         = "production"
    DB_AUTO_CREATE                      = "false"
    DB_POOL_SIZE                        = "2"
    DB_MAX_OVERFLOW                     = "0"
    DB_POOL_TIMEOUT                     = "30"
    STEAM_CATALOG_WORKER_MODE           = "scheduled"
    STEAM_CATALOG_WORKER_ENABLED        = "true"
    STEAM_CATALOG_REQUESTED_ONLY        = "true"
    STEAM_CATALOG_PAGE_SIZE             = "1000"
    STEAM_CATALOG_INTERVAL_MINUTES      = "360"
    STEAM_CATALOG_DETAIL_BATCH_SIZE     = "5"
    STEAM_CATALOG_REQUEST_DELAY_SECONDS = "2"
    STEAM_REVIEWS_IMPORT_LIMIT          = "10"
    OMP_NUM_THREADS                     = "1"
    OPENBLAS_NUM_THREADS                = "1"
    MKL_NUM_THREADS                     = "1"
  }
}

resource "google_project_service" "api" {
  for_each           = local.apis
  service            = each.key
  disable_on_destroy = false
}

resource "google_service_account" "identity" {
  for_each     = local.identities
  account_id   = "gametrack-${each.key}"
  display_name = "GameTrack ${each.key}"
  depends_on   = [google_project_service.api]
}

resource "google_project_iam_member" "sql_client" {
  for_each = local.db_clients
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.identity[each.key].email}"
}

resource "google_secret_manager_secret" "secret" {
  for_each  = local.secrets
  secret_id = each.key
  replication {
    auto {}
  }
  depends_on = [google_project_service.api]
  # Los valores se cargan por stdin/API; nunca entran al estado de Terraform.
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = local.secret_grants
  secret_id = google_secret_manager_secret.secret[each.value[1]].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.identity[each.value[0]].email}"
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "gametrack"
  format        = "DOCKER"
  depends_on    = [google_project_service.api]
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s"
      tag_state  = "ANY"
    }
  }
}

resource "google_sql_database_instance" "database" {
  name                = "gametrack-postgres"
  region              = var.region
  database_version    = "POSTGRES_17"
  deletion_protection = true
  settings {
    tier                        = "db-g1-small"
    edition                     = "ENTERPRISE"
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    disk_autoresize_limit       = 20
    deletion_protection_enabled = true
    ip_configuration {
      ipv4_enabled = true
      # Sin authorized_networks: acceso por la integración autenticada.
    }
    backup_configuration {
      enabled                        = true
      start_time                     = "06:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }
    maintenance_window {
      day  = 7
      hour = 7
    }
  }
  depends_on = [google_project_service.api]
}

resource "google_sql_database" "catalog" {
  name     = "gametrack"
  instance = google_sql_database_instance.database.name
}

resource "google_storage_bucket" "models" {
  name                        = "${var.project_id}-gametrack-models"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  depends_on = [google_project_service.api]
}

resource "google_storage_bucket" "migration" {
  name                        = "${var.project_id}-gametrack-migration"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.api]
}

resource "google_storage_bucket" "builds" {
  name                        = "${var.project_id}-gametrack-builds"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.api]
}

resource "google_storage_bucket_iam_member" "model_reader" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.identity["web"].email}"
}
resource "google_storage_bucket_iam_member" "model_writer" {
  bucket = google_storage_bucket.models.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.identity["train"].email}"
}
resource "google_storage_bucket_iam_member" "migration_reader" {
  bucket = google_storage_bucket.migration.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.identity["migrate"].email}"
}
resource "google_storage_bucket_iam_member" "build_source" {
  bucket = google_storage_bucket.builds.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.identity["build"].email}"
}
resource "google_artifact_registry_repository_iam_member" "builder" {
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.identity["build"].email}"
}
resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.identity["build"].email}"
}

resource "google_billing_budget" "pilot" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "GameTrack: consumo bruto mensual"
  budget_filter {
    projects               = ["projects/${data.google_project.current.number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
    calendar_period        = "MONTH"
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.8, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
  depends_on = [google_project_service.api]
}

locals {
  public_url = "https://gametrack-${data.google_project.current.number}.${var.region}.run.app"
  jobs = {
    migrate = {
      identity = "migrate", memory = "512Mi", timeout = "900s"
      command  = ["python"], args = ["-m", "alembic", "upgrade", "head"]
      secrets  = { DATABASE_URL = "gametrack-database-url" }
      env      = {}, source_mount = false
    }
    import = {
      identity = "migrate", memory = "1Gi", timeout = "3600s"
      command  = ["python"], args = ["scripts/copy_sqlite_to_postgres.py", "--source", "/migration/origen.db", "--execute"]
      secrets  = { DATABASE_URL = "gametrack-database-url" }
      env      = {}, source_mount = true
    }
    sync = {
      identity = "sync", memory = "1Gi", timeout = "900s"
      command  = ["python"], args = ["-m", "app.workers.steam_catalog_batch", "--seconds", "360", "--max-cycles", "60", "--max-db-mib", "2048"]
      secrets  = { DATABASE_URL = "gametrack-database-url", STEAM_API_KEY = "gametrack-steam-key" }
      # Sesión de datos, bloqueo dedicado y latido pueden coincidir.
      env = { DB_POOL_SIZE = "3" }, source_mount = false
    }
    train = {
      identity = "train", memory = "2Gi", timeout = "3600s"
      command  = ["python"], args = ["-m", "app.workers.train_cloud_model", "--data-label", var.model_data_label, "--version", var.training_version]
      secrets  = { DATABASE_URL = "gametrack-database-url" }
      env      = { MODEL_BUCKET = google_storage_bucket.models.name }, source_mount = false
    }
  }
}

resource "google_cloud_run_v2_job" "job" {
  for_each            = var.deploy_jobs ? local.jobs : {}
  name                = "gametrack-${each.key}"
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account       = google_service_account.identity[each.value.identity].email
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      max_retries           = 0
      timeout               = each.value.timeout
      dynamic "volumes" {
        for_each = each.value.source_mount ? [1] : []
        content {
          name = "migration"
          gcs {
            bucket        = google_storage_bucket.migration.name
            read_only     = true
            mount_options = ["uid=10001", "gid=10001"]
          }
        }
      }
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.database.connection_name]
        }
      }
      containers {
        image   = var.image
        command = each.value.command
        args    = each.value.args
        resources {
          limits = { cpu = "1", memory = each.value.memory }
        }
        dynamic "volume_mounts" {
          for_each = each.value.source_mount ? [1] : []
          content {
            name       = "migration"
            mount_path = "/migration"
          }
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
        dynamic "env" {
          for_each = merge(local.base_env, each.value.env)
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = each.value.secrets
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secret[env.value].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
  lifecycle {
    precondition {
      condition     = var.image != ""
      error_message = "Construir la imagen antes de crear los jobs."
    }
  }
  depends_on = [
    google_project_service.api, google_project_iam_member.sql_client,
    google_secret_manager_secret_iam_member.access,
    google_storage_bucket_iam_member.model_writer,
    google_storage_bucket_iam_member.migration_reader
  ]
}

resource "google_cloud_run_v2_service" "web" {
  count               = var.deploy_web ? 1 : 0
  name                = "gametrack"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true
  template {
    service_account                  = google_service_account.identity["web"].email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 8
    timeout                          = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    volumes {
      name = "models"
      gcs {
        bucket        = google_storage_bucket.models.name
        read_only     = true
        mount_options = ["uid=10001", "gid=10001"]
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.database.connection_name]
      }
    }
    containers {
      image = var.image
      # Cloud Run termina TLS antes del contenedor. Su proxy es el único
      # ingreso a esta instancia; Uvicorn debe reconocer el HTTPS original
      # para evitar que Steam redirija repetidamente hacia el mismo origen.
      command = ["python"]
      args = [
        "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
        "--proxy-headers", "--forwarded-allow-ips", "*"
      ]
      ports {
        container_port = 8000
      }
      resources {
        limits            = { cpu = "1", memory = "2Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      volume_mounts {
        name       = "models"
        mount_path = "/models"
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      dynamic "env" {
        for_each = merge(local.base_env, {
          PUBLIC_BASE_URL  = local.public_url
          LOCAL_MODEL_PATH = "/models/versions/${var.model_version}/local_model.npz"
          CORS_ORIGINS     = jsonencode([local.public_url])
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = {
          DATABASE_URL  = "gametrack-database-url"
          SECRET_KEY    = "gametrack-jwt-key"
          STEAM_API_KEY = "gametrack-steam-key"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 24
        http_get {
          path = "/healthz"
          port = 8000
        }
      }
      liveness_probe {
        timeout_seconds   = 5
        period_seconds    = 30
        failure_threshold = 3
        # La liveness mide el proceso; una caída de SQL no debe provocar
        # reinicios simultáneos de todas las instancias.
        http_get {
          path = "/api"
          port = 8000
        }
      }
    }
  }
  lifecycle {
    precondition {
      condition     = var.image != "" && var.deploy_jobs
      error_message = "Preparar imagen, jobs, migraciones y datos antes de publicar."
    }
  }
  depends_on = [
    google_project_service.api, google_project_iam_member.sql_client,
    google_secret_manager_secret_iam_member.access,
    google_storage_bucket_iam_member.model_reader
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.deploy_web ? 1 : 0
  location = var.region
  name     = google_cloud_run_v2_service.web[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  count    = var.deploy_jobs ? 1 : 0
  location = var.region
  name     = google_cloud_run_v2_job.job["sync"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.identity["scheduler"].email}"
}

resource "google_cloud_scheduler_job" "sync" {
  count     = var.enable_scheduler ? 1 : 0
  name      = "gametrack-steam-six-hourly"
  region    = var.region
  schedule  = "17 */6 * * *"
  time_zone = "Etc/UTC"
  # Crear sólo después de migrar, verificar la web y probar una tanda manual.
  paused           = false
  attempt_deadline = "30s"
  http_target {
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/gametrack-sync:run"
    http_method = "POST"
    headers     = { "Content-Type" = "application/json" }
    body        = base64encode("{}")
    oauth_token {
      service_account_email = google_service_account.identity["scheduler"].email
    }
  }
  retry_config {
    retry_count          = 0
    min_backoff_duration = "5s"
    max_backoff_duration = "3600s"
    max_doublings        = 5
  }
  lifecycle {
    precondition {
      condition     = var.deploy_jobs && var.deploy_web
      error_message = "Publicar y verificar la web antes de habilitar el horario."
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.scheduler]
}

variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Usar el ID del proyecto de Google Cloud."
  }
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "image" {
  type    = string
  default = ""
  validation {
    condition     = var.image == "" || can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "Fijar la imagen por digest sha256, no por una etiqueta mutable."
  }
}
variable "deploy_jobs" {
  type    = bool
  default = false
}
variable "deploy_web" {
  type    = bool
  default = false
}
variable "enable_scheduler" {
  type    = bool
  default = false
}
variable "model_version" {
  type    = string
  default = "untrained"
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$", var.model_version))
    error_message = "La versión debe ser un identificador sin barras ni espacios."
  }
}
variable "model_data_label" {
  type    = string
  default = "observed"
  validation {
    condition     = var.model_data_label == "observed"
    error_message = "Sólo se permiten datos reales (observed)."
  }
}
variable "billing_account_id" {
  type    = string
  default = ""
}
variable "training_version" {
  type    = string
  default = "untrained"
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$", var.training_version))
    error_message = "La versión de entrenamiento debe ser un identificador sin barras."
  }
}
variable "monthly_budget_usd" {
  type    = number
  default = 60
  validation {
    condition     = var.monthly_budget_usd >= 1 && floor(var.monthly_budget_usd) == var.monthly_budget_usd
    error_message = "El presupuesto debe ser un número entero positivo de USD."
  }
}

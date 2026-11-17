variable "endpoint" {
  description = "Floci (local AWS emulator) endpoint."
  type        = string
  default     = "http://localhost:4566"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "tokenprofiler-local"
}

variable "bootstrap_api_key" {
  description = "Pre-chosen bootstrap key so the console and demo scripts can use it."
  type        = string
  default     = null
  sensitive   = true
}

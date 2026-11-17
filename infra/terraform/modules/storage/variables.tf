variable "name" {
  description = "Name prefix, e.g. tokenprofiler-prod. The bucket is named <name>-reports."
  type        = string
}

variable "versioning" {
  description = "Keep object versions."
  type        = bool
  default     = true
}

variable "retention_days" {
  description = "Days before archived reports expire."
  type        = number
  default     = 365
}

variable "noncurrent_days" {
  description = "Days before superseded versions of a model file expire."
  type        = number
  default     = 30
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty bucket (never in production)."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for SSE-KMS. Null uses S3-managed AES256."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

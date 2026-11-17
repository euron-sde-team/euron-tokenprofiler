variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "tokenprofiler"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "image_tag" {
  description = "Tag of the api and console images in ECR."
  type        = string
  default     = "latest"
}

variable "instance_type" {
  description = "t3.medium to t3.xlarge: the workload is SQLite and Python."
  type        = string
  default     = "t3.large"
}

variable "volume_gb" {
  type    = number
  default = 200
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Null serves plain HTTP (acceptable for dev only)."
  type        = string
  default     = null
}

variable "ingress_cidr" {
  description = "CIDR allowed to reach the console and API."
  type        = string
  default     = "0.0.0.0/0"
}

variable "alert_email" {
  type    = string
  default = null
}


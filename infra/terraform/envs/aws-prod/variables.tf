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
  default = "prod"
}

variable "image_tag" {
  description = "Tag of the api and console images in ECR."
  type        = string
  default     = "v0.1.0"
}

variable "instance_type" {
  description = "t3.medium to t3.xlarge: the workload is SQLite and Python."
  type        = string
  default     = "t3.xlarge"
}

variable "volume_gb" {
  type    = number
  default = 500
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Null serves plain HTTP (acceptable for dev only)."
  type        = string
  default     = null
}

variable "ingress_cidr" {
  description = "CIDR allowed to reach the console and API."
  type        = string
  default     = "10.0.0.0/8"
}

variable "alert_email" {
  type    = string
  default = null
}


variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Private subnets the host may run in."
  type        = list(string)
}

variable "alb_security_group_id" {
  type = string
}

variable "target_group_arns" {
  type = list(string)
}

variable "instance_type" {
  description = "t3.medium is plenty for tens of millions of events; the workload is SQLite and Python."
  type        = string
  default     = "t3.medium"
}

variable "volume_gb" {
  description = "Root volume (holds the SQLite databases). 100 GB holds roughly 200 million events."
  type        = number
  default     = 100
}

variable "secret_arn" {
  type = string
}

variable "reports_bucket" {
  type = string
}

variable "reports_bucket_arn" {
  type = string
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "api_image" {
  type = string
}

variable "console_image" {
  type = string
}

variable "api_port" {
  type    = number
  default = 8380
}

variable "console_port" {
  type    = number
  default = 3300
}

variable "tags" {
  type    = map(string)
  default = {}
}

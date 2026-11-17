variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "api_port" {
  type    = number
  default = 8380
}

variable "console_port" {
  type    = number
  default = 3300
}

variable "certificate_arn" {
  description = "ACM certificate. When null the listener is plain HTTP (dev only)."
  type        = string
  default     = null
}

variable "ingress_cidr" {
  description = "Who may reach the load balancer. Restrict this for internal tools."
  type        = string
  default     = "0.0.0.0/0"
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}

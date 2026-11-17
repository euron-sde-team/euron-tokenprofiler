variable "name" {
  type = string
}

variable "cidr" {
  type    = string
  default = "10.50.0.0/16"
}

variable "az_count" {
  type    = number
  default = 2
}

variable "single_nat_gateway" {
  description = "One shared NAT (cheaper, dev) instead of one per AZ (resilient, prod)."
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}

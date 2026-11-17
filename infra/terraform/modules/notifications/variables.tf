variable "name" {
  type = string
}

variable "alert_email" {
  description = "Optional address subscribed to alarm notifications (must confirm the subscription)."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}

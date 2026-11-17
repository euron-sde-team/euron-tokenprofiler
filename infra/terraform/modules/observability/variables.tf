variable "name" {
  type = string
}

variable "alarm_topic_arn" {
  description = "SNS topic that receives alarm notifications."
  type        = string
}

variable "alb_arn_suffix" {
  type = string
}

variable "api_target_group_arn_suffix" {
  type = string
}

variable "autoscaling_group_name" {
  type = string
}

variable "latency_threshold_s" {
  description = "p95 response time that raises the latency alarm. Generations are slow by nature: keep this generous."
  type        = number
  default     = 60
}

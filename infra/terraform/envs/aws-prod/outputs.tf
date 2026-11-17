output "url" {
  description = "Console and API entry point."
  value       = "${var.certificate_arn == null ? "http" : "https"}://${module.alb.dns_name}"
}

output "ecr_repositories" {
  value = module.ecr.repository_urls
}

output "reports_bucket" {
  description = "Archived usage reports and database backups."
  value       = module.storage.bucket
}

output "app_secret_arn" {
  description = "Holds the bootstrap API key: aws secretsmanager get-secret-value --secret-id <arn>"
  value       = module.secrets.secret_arn
}

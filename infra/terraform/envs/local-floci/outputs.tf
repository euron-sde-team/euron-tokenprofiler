output "reports_bucket" {
  value = module.storage.bucket
}

output "secrets_arn" {
  value = module.secrets.secret_arn
}

output "bootstrap_api_key" {
  value     = module.secrets.bootstrap_api_key
  sensitive = true
}

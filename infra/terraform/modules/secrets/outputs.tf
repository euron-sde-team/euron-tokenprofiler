output "secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "bootstrap_api_key" {
  description = "The all-scopes key installed at API startup. Rotate it by creating scoped keys and removing this one."
  value       = local.bootstrap_api_key
  sensitive   = true
}

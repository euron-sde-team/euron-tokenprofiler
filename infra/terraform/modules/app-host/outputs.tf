output "security_group_id" {
  value = aws_security_group.host.id
}

output "role_name" {
  value = aws_iam_role.host.name
}

output "autoscaling_group_name" {
  value = aws_autoscaling_group.host.name
}

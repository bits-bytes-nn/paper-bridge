output "client_security_group_id" {
  description = "ID of the client security group"
  value       = aws_security_group.client.id
}

output "client_role_arn" {
  description = "ARN of the client IAM role"
  value       = aws_iam_role.client.arn
}

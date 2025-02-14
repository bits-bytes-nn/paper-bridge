output "security_group_id" {
  description = "ID of the client security group"
  value       = aws_security_group.client.id
}

output "role_arn" {
  description = "ARN of the client IAM role"
  value       = aws_iam_role.client.arn
}

output "notebook_url" {
  description = "URL of the SageMaker notebook instance"
  value       = var.deploy_notebook ? aws_sagemaker_notebook_instance.notebook[0].url : null
}

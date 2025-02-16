output "bastion_host_security_group_id" {
  description = "ID of the bastion host security group"
  value       = var.deploy_bastion_host ? aws_security_group.bastion_host[0].id : null
}


output "app_client_security_group_id" {
  description = "ID of the app client security group"
  value       = aws_security_group.app_client.id
}

output "workload_role_arn" {
  description = "ARN of the workload IAM role"
  value       = aws_iam_role.workload.arn
}

output "bastion_host_instance_id" {
  description = "ID of the bastion host EC2 instance"
  value       = var.deploy_bastion_host ? aws_instance.bastion_host[0].id : null
}

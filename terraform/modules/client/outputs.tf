output "security_group_id" {
  description = "ID of the client security group"
  value       = aws_security_group.client.id
}

output "iam_role" {
  description = "Client IAM role details"
  value = {
    arn  = aws_iam_role.client.arn
    name = aws_iam_role.client.name
    id   = aws_iam_role.client.id
  }
}

output "bedrock_inference_role_arn" {
  description = "ARN of the Bedrock inference IAM role"
  value       = aws_iam_role.bedrock_inference.arn
}

output "batch_job_queue" {
  description = "Name of the AWS Batch job queue"
  value       = aws_batch_job_queue.this.name
}

output "batch_job_definition" {
  description = "Name of the AWS Batch job definition for the indexing job"
  value       = aws_batch_job_definition.indexer.name
}

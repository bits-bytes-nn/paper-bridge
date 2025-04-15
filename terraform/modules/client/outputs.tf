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

# Batch resources
output "batch_job_queue" {
  description = "Name of the AWS Batch job queue for indexer"
  value       = var.use_graph_rag ? aws_batch_job_queue.indexer[0].name : null
}

output "batch_job_definition" {
  description = "Name of the AWS Batch job definition for the indexing job"
  value       = var.use_graph_rag ? aws_batch_job_definition.indexer[0].name : null
}

output "batch_job_queue_summarizer" {
  description = "Name of the AWS Batch job queue for summarizer"
  value       = aws_batch_job_queue.summarizer.name
}

output "batch_job_definition_summarizer" {
  description = "Name of the AWS Batch job definition for the summarizer job"
  value       = aws_batch_job_definition.summarizer.name
}

# ECR repositories
output "ecr_repository_indexer" {
  description = "ECR repository URL for the indexer image"
  value       = var.use_graph_rag ? aws_ecr_repository.indexer[0].repository_url : null
}

output "ecr_repository_cleaner" {
  description = "ECR repository URL for the cleaner image"
  value       = var.use_graph_rag ? aws_ecr_repository.cleaner[0].repository_url : null
}

output "ecr_repository_summarizer" {
  description = "ECR repository URL for the summarizer image"
  value       = aws_ecr_repository.summarizer.repository_url
}

# CodeBuild projects
output "codebuild_project_indexer" {
  description = "CodeBuild project name for indexer"
  value       = var.use_graph_rag ? aws_codebuild_project.indexer[0].name : null
}

output "codebuild_project_cleaner" {
  description = "CodeBuild project name for cleaner"
  value       = var.use_graph_rag ? aws_codebuild_project.cleaner[0].name : null
}

output "codebuild_project_summarizer" {
  description = "CodeBuild project name for summarizer"
  value       = aws_codebuild_project.summarizer.name
}

# SNS topic
output "sns_topic_arn" {
  description = "ARN of the SNS topic for notifications"
  value       = aws_sns_topic.this.arn
}

# SSM parameters
output "ssm_parameters" {
  description = "SSM parameter names"
  value = {
    bedrock_inference_role_arn = aws_ssm_parameter.bedrock_inference.name
    business_slack_bot_token    = var.business_slack_bot_token != null ? aws_ssm_parameter.business_slack_bot_token[0].name : null
    business_slack_channel_id   = var.business_slack_channel_id != null ? aws_ssm_parameter.business_slack_channel_id[0].name : null
    llama_cloud_api_key         = var.llama_cloud_api_key != null ? aws_ssm_parameter.llama_cloud_api_key[0].name : null
    personal_slack_bot_token    = var.personal_slack_bot_token != null ? aws_ssm_parameter.personal_slack_bot_token[0].name : null
    personal_slack_channel_id   = var.personal_slack_channel_id != null ? aws_ssm_parameter.personal_slack_channel_id[0].name : null
    upstage_api_key             = var.upstage_api_key != null ? aws_ssm_parameter.upstage_api_key[0].name : null
    batch_job_queue_indexer     = var.use_graph_rag ? aws_ssm_parameter.batch_job_queue_indexer[0].name : null
    batch_job_definition_indexer = var.use_graph_rag ? aws_ssm_parameter.batch_job_definition_indexer[0].name : null
    batch_job_queue_summarizer  = aws_ssm_parameter.batch_job_queue_summarizer.name
    batch_job_definition_summarizer = aws_ssm_parameter.batch_job_definition_summarizer.name
  }
}

output "neptune_endpoint" {
  description = "Neptune cluster endpoint URL"
  value       = module.neptune.cluster.endpoint
}

output "opensearch_endpoint" {
  description = "OpenSearch Serverless collection endpoint URL"
  value       = module.opensearch.collection.collection_endpoint
}

output "bedrock_inference_role_arn" {
  description = "ARN of the Bedrock batch inference IAM role"
  value       = module.client.bedrock_inference_role_arn
}

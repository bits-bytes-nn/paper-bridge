output "vpn_endpoint" {
  description = "VPN endpoint details for client connections"
  value       = var.enable_vpn ? module.base.vpn_endpoint : null
}

output "neptune_endpoint" {
  description = "Neptune cluster endpoint URL"
  value       = var.use_graph_rag ? module.neptune[0].cluster.endpoint : null
}

output "opensearch_endpoint" {
  description = "OpenSearch Serverless collection endpoint URL"
  value       = var.use_graph_rag ? module.opensearch[0].collection.collection_endpoint : null
}

output "bedrock_inference_role_arn" {
  description = "ARN of the Bedrock batch inference IAM role"
  value       = module.client.bedrock_inference_role_arn
}

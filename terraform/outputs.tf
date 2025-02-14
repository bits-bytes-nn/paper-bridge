output "client" {
  description = "SageMaker notebook URL"
  value       = module.client.notebook_url
}

output "neptune" {
  description = "Neptune cluster endpoint"
  value       = module.neptune.cluster.cluster_endpoint
}

output "opensearch" {
  description = "OpenSearch collection endpoint"
  value       = module.opensearch.collection.collection_endpoint
}

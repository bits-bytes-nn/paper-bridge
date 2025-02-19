output "neptune_endpoint" {
  description = "Neptune cluster endpoint URL"
  value       = module.neptune.cluster.cluster_endpoint
}

output "opensearch_endpoint" {
  description = "OpenSearch Serverless collection endpoint URL"
  value       = module.opensearch.collection.collection_endpoint
}

output "bastion_instance_id" {
  description = "ID of the bastion host instance"
  value       = module.workload.bastion_host_instance_id
}

output "neptune_endpoint" {
  description = "Neptune cluster endpoint URL"
  value       = module.neptune.cluster.cluster_endpoint
}

output "opensearch_endpoint" {
  description = "OpenSearch Serverless collection endpoint URL"
  value       = module.opensearch.collection.collection_endpoint
}

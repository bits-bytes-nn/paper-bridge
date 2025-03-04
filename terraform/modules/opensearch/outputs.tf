output "collection" {
  description = "OpenSearch Serverless collection details"
  value = {
    id                  = aws_opensearchserverless_collection.this.id
    name                = aws_opensearchserverless_collection.this.name
    dashboard_endpoint  = aws_opensearchserverless_collection.this.dashboard_endpoint
    collection_endpoint = aws_opensearchserverless_collection.this.collection_endpoint
    ssm_param           = aws_ssm_parameter.opensearch_endpoint.name
  }
}

output "collection_id" {
  description = "OpenSearch Serverless collection ID"
  value       = aws_opensearchserverless_collection.this.id
}

output "collection_name" {
  description = "OpenSearch Serverless collection name"
  value       = aws_opensearchserverless_collection.this.name
}

output "dashboard_endpoint" {
  description = "OpenSearch Serverless dashboard endpoint URL"
  value       = aws_opensearchserverless_collection.this.dashboard_endpoint
}

output "collection_endpoint" {
  description = "OpenSearch Serverless collection endpoint URL"
  value       = aws_opensearchserverless_collection.this.collection_endpoint
}

output "ssm_parameter_name" {
  description = "SSM parameter name storing the OpenSearch endpoint"
  value       = aws_ssm_parameter.opensearch_endpoint.name
}

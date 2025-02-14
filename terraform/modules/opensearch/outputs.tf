output "collection" {
 description = "OpenSearch Serverless collection details"
 value = {
   id                  = aws_opensearchserverless_collection.collection.id
   name                = aws_opensearchserverless_collection.collection.name
   dashboard_endpoint  = aws_opensearchserverless_collection.collection.dashboard_endpoint
   collection_endpoint = aws_opensearchserverless_collection.collection.collection_endpoint
   security_group_id   = aws_security_group.opensearch.id
   ssm_param           = aws_ssm_parameter.opensearch_endpoint.name
 }
}

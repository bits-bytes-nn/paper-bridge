output "cluster" {
 description = "Neptune cluster details"
 value = {
   id                = aws_neptune_cluster.cluster.id
   cluster_endpoint  = aws_neptune_cluster.cluster.endpoint
   security_group_id = aws_security_group.neptune.id
   ssm_param         = aws_ssm_parameter.neptune_endpoint.name
 }
}
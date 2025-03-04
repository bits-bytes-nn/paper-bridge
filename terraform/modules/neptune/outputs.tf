output "cluster" {
  description = "Neptune cluster details and connection information"
  value = {
    id                = aws_neptune_cluster.this.id
    endpoint          = aws_neptune_cluster.this.endpoint
    reader_endpoint   = aws_neptune_cluster.this.reader_endpoint
    port              = aws_neptune_cluster.this.port
    security_group_id = aws_security_group.neptune.id
    ssm_parameter     = aws_ssm_parameter.neptune_endpoint.name
    engine_version    = aws_neptune_cluster.this.engine_version
  }
}

output "instance" {
  description = "Neptune instance details"
  value = {
    id            = aws_neptune_cluster_instance.this.id
    instance_type = aws_neptune_cluster_instance.this.instance_class
  }
}

output "endpoints" {
  description = "Neptune cluster endpoints"
  value = {
    writer = aws_neptune_cluster.this.endpoint
    reader = aws_neptune_cluster.this.reader_endpoint
  }
}

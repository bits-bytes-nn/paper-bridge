data "aws_region" "current" {}

locals {
  neptune_family = "neptune${split(".", var.engine_version)[0]}.${split(".", var.engine_version)[1]}"
  name_prefix    = var.project_name
}

resource "aws_neptune_subnet_group" "this" {
  name        = local.name_prefix
  subnet_ids  = var.private_subnet_ids
  description = "Neptune subnet group for ${local.name_prefix}"

  tags = merge(var.tags, {
    Name = local.name_prefix
  })
}

resource "aws_neptune_cluster_parameter_group" "this" {
  name        = local.name_prefix
  family      = local.neptune_family
  description = "Neptune cluster parameter group for ${local.name_prefix}"

  tags = merge(var.tags, {
    Name = local.name_prefix
  })
}

resource "aws_neptune_parameter_group" "this" {
  name        = local.name_prefix
  family      = local.neptune_family
  description = "Neptune instance parameter group for ${local.name_prefix}"

  parameter {
    name  = "neptune_query_timeout"
    value = "60000"
  }

  tags = merge(var.tags, {
    Name = local.name_prefix
  })
}

resource "aws_security_group" "neptune" {
  name        = "${local.name_prefix}-neptune"
  description = "Security group for Neptune cluster"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8182
    to_port         = 8182
    protocol        = "tcp"
    security_groups = concat(var.client_security_group_ids, var.vpn_security_group_ids)
    description     = "Allow inbound access from client security groups"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-neptune"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                   = local.name_prefix
  enable_cloudwatch_logs_exports       = var.enable_audit_log ? ["audit"] : []
  engine                               = "neptune"
  engine_version                       = var.engine_version
  neptune_subnet_group_name            = aws_neptune_subnet_group.this.name
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.this.name
  port                                 = 8182
  skip_final_snapshot                  = true
  storage_encrypted                    = true
  vpc_security_group_ids               = [aws_security_group.neptune.id]
  apply_immediately                    = var.apply_immediately

  serverless_v2_scaling_configuration {
    min_capacity = var.min_ncu
    max_capacity = var.max_ncu
  }

  tags = merge(var.tags, {
    Name = local.name_prefix
  })

  depends_on = [
    aws_neptune_subnet_group.this,
    aws_neptune_cluster_parameter_group.this,
    aws_security_group.neptune
  ]
}

resource "aws_neptune_cluster_instance" "this" {
  identifier                   = local.name_prefix
  cluster_identifier           = aws_neptune_cluster.this.id
  instance_class               = var.db_instance_type
  engine                       = "neptune"
  engine_version               = var.engine_version
  neptune_parameter_group_name = aws_neptune_parameter_group.this.name
  auto_minor_version_upgrade   = true
  apply_immediately            = var.apply_immediately

  tags = merge(var.tags, {
    Name = local.name_prefix
  })

  depends_on = [
    aws_neptune_cluster.this,
    aws_neptune_parameter_group.this
  ]
}

resource "aws_ssm_parameter" "neptune_endpoint" {
  name        = "/${local.name_prefix}/neptune/endpoint"
  description = "Neptune cluster endpoint"
  type        = "String"
  value       = aws_neptune_cluster.this.endpoint

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-neptune-endpoint"
  })

  depends_on = [aws_neptune_cluster.this]
}

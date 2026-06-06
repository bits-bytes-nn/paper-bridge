data "aws_region" "current" {}

locals {
  name_prefix    = var.project_name
  neptune_family = "neptune${split(".", var.engine_version)[0]}.${split(".", var.engine_version)[1]}"
}

resource "aws_neptune_subnet_group" "this" {
  name        = local.name_prefix
  subnet_ids  = var.private_subnet_ids
  description = "Neptune subnet group for ${local.name_prefix}"

  tags = var.tags
}

resource "aws_neptune_cluster_parameter_group" "this" {
  # name_prefix (not a fixed name) + create_before_destroy so an engine-family
  # change (e.g. neptune1.2 -> neptune1.4) can create the replacement group under
  # a new unique name and attach it to the cluster BEFORE the old one is deleted.
  # A fixed name would deadlock: the old group can't be deleted while attached and
  # the new one can't take the same name.
  name_prefix = "${local.name_prefix}-"
  family      = local.neptune_family
  description = "Neptune cluster parameter group for ${local.name_prefix}"

  lifecycle {
    create_before_destroy = true
  }

  tags = var.tags
}

resource "aws_neptune_parameter_group" "this" {
  name_prefix = "${local.name_prefix}-"
  family      = local.neptune_family
  description = "Neptune instance parameter group for ${local.name_prefix}"

  parameter {
    name  = "neptune_query_timeout"
    value = "60000"
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = var.tags
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

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                   = local.name_prefix
  enable_cloudwatch_logs_exports       = var.enable_audit_log ? ["audit"] : []
  engine                               = "neptune"
  engine_version                       = var.engine_version
  allow_major_version_upgrade          = var.allow_major_version_upgrade
  neptune_subnet_group_name            = aws_neptune_subnet_group.this.name
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.this.name
  port                                 = 8182
  skip_final_snapshot                  = var.skip_final_snapshot
  final_snapshot_identifier            = var.skip_final_snapshot ? null : "${local.name_prefix}-final"
  storage_encrypted                    = true
  vpc_security_group_ids               = [aws_security_group.neptune.id]
  apply_immediately                    = var.apply_immediately

  serverless_v2_scaling_configuration {
    min_capacity = var.min_ncu
    max_capacity = var.max_ncu
  }

  tags = var.tags

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

  tags = var.tags

  depends_on = [
    aws_neptune_cluster.this,
    aws_neptune_parameter_group.this
  ]
}

resource "aws_ssm_parameter" "neptune_endpoint" {
  name  = "/${local.name_prefix}-${var.stage}/neptune-endpoint"
  type  = "String"
  value = aws_neptune_cluster.this.endpoint
  tags  = var.tags

  depends_on = [aws_neptune_cluster.this]
}

data "aws_region" "current" {}

resource "aws_neptune_subnet_group" "subnet" {
  name       = "${var.project_name}-subnet"
  subnet_ids = var.private_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.project_name}-subnet"
  })
}

resource "aws_neptune_cluster_parameter_group" "cluster_param" {
  name        = "${var.project_name}-cluster-param"
  family      = "neptune${split(".", var.engine_version)[0]}.${split(".", var.engine_version)[1]}"
  description = "Neptune cluster parameter group"

  tags = merge(var.tags, {
    Name = "${var.project_name}-cluster-param"
  })
}

resource "aws_neptune_parameter_group" "instance_param" {
  name        = "${var.project_name}-instance-param"
  family      = "neptune${split(".", var.engine_version)[0]}.${split(".", var.engine_version)[1]}"
  description = "Neptune instance parameter group"

  parameter {
    name  = "neptune_query_timeout"
    value = "60000"
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-instance-param"
  })
}

resource "aws_neptune_cluster" "cluster" {
  cluster_identifier                    = "${var.project_name}-cluster"
  enable_cloudwatch_logs_exports        = var.enable_audit_log ? ["audit"] : []
  engine                               = "neptune"
  engine_version                       = var.engine_version
  neptune_subnet_group_name            = aws_neptune_subnet_group.subnet.name
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.cluster_param.name
  port                                 = 8182
  skip_final_snapshot                  = true
  storage_encrypted                    = true
  vpc_security_group_ids               = [aws_security_group.neptune.id]

  serverless_v2_scaling_configuration {
    min_capacity = var.min_ncu
    max_capacity = var.max_ncu
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-cluster"
  })
}

resource "aws_neptune_cluster_instance" "instance" {
  identifier                    = "${var.project_name}-instance"
  cluster_identifier           = aws_neptune_cluster.cluster.id
  instance_class               = var.db_instance_type
  engine                       = "neptune"
  engine_version               = var.engine_version
  neptune_parameter_group_name = aws_neptune_parameter_group.instance_param.name
  auto_minor_version_upgrade   = true

  tags = merge(var.tags, {
    Name = "${var.project_name}-instance"
  })
}

resource "aws_security_group" "neptune" {
  name_prefix = "${var.project_name}-neptune-sg"
  description = "Security group for Neptune cluster"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8182
    to_port         = 8182
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
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
    Name = "${var.project_name}-neptune-sg"
  })
}

resource "aws_ssm_parameter" "neptune_endpoint" {
  name        = "/${var.project_name}/neptune/cluster-endpoint"
  description = "Neptune cluster endpoint"
  type        = "String"
  value       = aws_neptune_cluster.cluster.endpoint
  tags = merge(var.tags, {
    Name = "${var.project_name}-neptune-endpoint"
  })
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_opensearchserverless_vpc_endpoint" "vpce" {
  name               = "${var.project_name}-vpce"
  subnet_ids         = var.private_subnet_ids
  vpc_id             = var.vpc_id
  security_group_ids = [aws_security_group.opensearch.id]
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name        = "${var.project_name}-encryption"
  type        = "encryption"
  description = "Encryption policy for OpenSearch Serverless"
  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${var.project_name}-collection"]
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name        = "${var.project_name}-network"
  type        = "network"
  description = "Network security policy for OpenSearch Serverless"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-collection"]
      },
      {
        ResourceType = "dashboard"
        Resource     = ["collection/${var.project_name}-collection"]
      }
    ]
    AllowFromPublic = false
    SourceVPCEs     = [aws_opensearchserverless_vpc_endpoint.vpce.id]
  }])
}

resource "aws_opensearchserverless_access_policy" "client_access" {
  name        = "${var.project_name}-access"
  type        = "data"
  description = "Data access policy for OpenSearch Serverless"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.project_name}-collection"]
        Permission   = ["aoss:*"]
      },
      {
        ResourceType = "index"
        Resource     = ["index/${var.project_name}-collection/*"]
        Permission   = ["aoss:*"]
      }
    ]
    Principal = [var.client_role_arn]
  }])
}

resource "aws_iam_policy" "opensearch_api_access" {
  name = "${var.project_name}-opensearch-api-access"
  path = "/"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "aoss:APIAccessAll"
      Resource = [
        "arn:aws:aoss:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:collection/${aws_opensearchserverless_collection.collection.id}"
      ]
    }]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-opensearch-api-access"
  })
}

resource "aws_iam_role_policy_attachment" "client_opensearch_access" {
  role       = split("/", var.client_role_arn)[1]
  policy_arn = aws_iam_policy.opensearch_api_access.arn
}

resource "aws_opensearchserverless_collection" "collection" {
  name        = "${var.project_name}-collection"
  type        = "VECTORSEARCH"
  description = "Vector search collection for OpenSearch Serverless"
  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
    aws_opensearchserverless_access_policy.client_access
  ]

  tags = merge(var.tags, {
    Name = "${var.project_name}-collection"
  })
}

resource "aws_security_group" "opensearch" {
  name        = "${var.project_name}-opensearch-sg"
  description = "Security group for OpenSearch VPC endpoint"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTPS from client security groups"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  egress {
    description = "HTTPS to OpenSearch service"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-opensearch-sg"
  })
}

resource "aws_ssm_parameter" "opensearch_endpoint" {
  name        = "/${var.project_name}/opensearch/endpoint"
  description = "OpenSearch Serverless collection endpoint"
  type        = "String"
  value       = aws_opensearchserverless_collection.collection.collection_endpoint
  tags = merge(var.tags, {
    Name = "${var.project_name}-opensearch-endpoint"
  })
}

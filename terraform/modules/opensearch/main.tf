data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name_prefix         = var.project_name
  collection_resource = "collection/${var.project_name}"
  index_resource      = "index/${var.project_name}/*"
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name        = "${local.name_prefix}-encryption"
  type        = "encryption"
  description = "Encryption policy for OpenSearch Serverless"
  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = [local.collection_resource]
    }]
    AWSOwnedKey = true
  })
}

# Security group for the OpenSearch Serverless VPC endpoint.
# Allows inbound HTTPS only from the client (and optional VPN) security groups,
# keeping the collection reachable exclusively from within the VPC.
resource "aws_security_group" "opensearch_vpce" {
  name        = "${local.name_prefix}-opensearch-vpce"
  description = "Security group for the OpenSearch Serverless VPC endpoint"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = concat(var.client_security_group_ids, var.vpn_security_group_ids)
    description     = "Allow inbound HTTPS from client/VPN security groups"
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

# VPC endpoint that makes the collection reachable privately from inside the VPC.
resource "aws_opensearchserverless_vpc_endpoint" "this" {
  name               = "${local.name_prefix}-vpce"
  vpc_id             = var.vpc_id
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.opensearch_vpce.id]
}

resource "aws_opensearchserverless_security_policy" "network" {
  name        = "${local.name_prefix}-network"
  type        = "network"
  description = "Network policy for OpenSearch Serverless"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = [local.collection_resource]
      },
      {
        ResourceType = "dashboard"
        Resource     = [local.collection_resource]
      }
    ]
    # VPC-only by default: access is restricted to the VPC endpoint above.
    # Public access is gated behind an explicit opt-in (default false).
    AllowFromPublic = var.allow_public_access
    SourceVPCEs     = var.allow_public_access ? null : [aws_opensearchserverless_vpc_endpoint.this.id]
  }])
}

resource "aws_opensearchserverless_access_policy" "data" {
  name        = "${local.name_prefix}-data"
  type        = "data"
  description = "Data access policy for OpenSearch Serverless"
  policy = jsonencode([{
    Rules = [
      {
        # Collection-level: describe only. Item/index mutation is granted at the
        # index scope below (least-privilege over the former "aoss:*").
        ResourceType = "collection"
        Resource     = [local.collection_resource]
        Permission   = ["aoss:DescribeCollectionItems"]
      },
      {
        # Index-level: the GraphRAG toolkit creates/updates indices and
        # reads/writes documents (indexer writes + cleaner deletes; retriever
        # reads). This is the full set of data-plane actions actually used.
        ResourceType = "index"
        Resource     = [local.index_resource]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
        ]
      }
    ]
    Principal = concat(
      [var.client_role_arn],
      var.enable_vpn ? ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/${var.client_user_name}"] : []
    )
  }])
}

resource "aws_iam_policy" "opensearch_access" {
  name        = "${local.name_prefix}-opensearch-access"
  path        = "/"
  description = "Policy for OpenSearch Serverless API and Dashboard access"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "aoss:APIAccessAll",
          "aoss:DashboardsAccessAll"
        ]
        Resource = [
          "arn:aws:aoss:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:collection/${aws_opensearchserverless_collection.this.id}"
        ]
      }
    ]
  })

  tags = var.tags
}
resource "aws_iam_user_policy_attachment" "client_user_attachment" {
  count      = var.enable_vpn ? 1 : 0
  user       = var.client_user_name
  policy_arn = aws_iam_policy.opensearch_access.arn
}

resource "aws_iam_role_policy_attachment" "client_role_attachment" {
  role       = var.client_role_name != null ? var.client_role_name : split("/", var.client_role_arn)[1]
  policy_arn = aws_iam_policy.opensearch_access.arn
}

resource "aws_opensearchserverless_collection" "this" {
  name        = local.name_prefix
  type        = "VECTORSEARCH"
  description = "Vector search collection for OpenSearch Serverless"

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
    aws_opensearchserverless_access_policy.data
  ]

  tags = var.tags
}

resource "aws_ssm_parameter" "opensearch_endpoint" {
  name  = "/${local.name_prefix}-${var.stage}/opensearch-endpoint"
  type  = "String"
  value = aws_opensearchserverless_collection.this.collection_endpoint
  tags  = var.tags
}

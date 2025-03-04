data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  collection_resource = "collection/${var.project_name}"
  index_resource      = "index/${var.project_name}/*"
  policy_name_prefix  = var.project_name
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name        = "${local.policy_name_prefix}-encryption"
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

resource "aws_opensearchserverless_security_policy" "network" {
  name        = "${local.policy_name_prefix}-network"
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
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_access_policy" "data" {
  name        = "${local.policy_name_prefix}-data"
  type        = "data"
  description = "Data access policy for OpenSearch Serverless"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = [local.collection_resource]
        Permission   = ["aoss:*"]
      },
      {
        ResourceType = "index"
        Resource     = [local.index_resource]
        Permission   = ["aoss:*"]
      }
    ]
    Principal = concat(
      [var.client_role_arn],
      var.enable_vpn ? ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/${var.client_user_name}"] : []
    )
  }])
}

resource "aws_iam_policy" "opensearch_access" {
  name        = "${local.policy_name_prefix}-opensearch-access"
  path        = "/"
  description = "Policy for OpenSearch Serverless API and Dashboard access"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = [
          "aoss:APIAccessAll",
          "aoss:DashboardsAccessAll"
        ]
        Resource = [
          "arn:aws:aoss:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:collection/${aws_opensearchserverless_collection.this.id}"
        ]
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-opensearch-access"
  })
}

resource "aws_iam_role_policy_attachment" "client_role_attachment" {
  role       = split("/", var.client_role_arn)[1]
  policy_arn = aws_iam_policy.opensearch_access.arn
}

resource "aws_iam_user_policy_attachment" "client_user_attachment" {
  count      = var.enable_vpn ? 1 : 0
  user       = var.client_user_name
  policy_arn = aws_iam_policy.opensearch_access.arn
}

resource "aws_opensearchserverless_collection" "this" {
  name        = var.project_name
  type        = "VECTORSEARCH"
  description = "Vector search collection for OpenSearch Serverless"

  depends_on = [
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
    aws_opensearchserverless_access_policy.data
  ]

  tags = merge(var.tags, {
    Name = var.project_name
  })
}

resource "aws_ssm_parameter" "opensearch_endpoint" {
  name        = "/${var.project_name}/opensearch/endpoint"
  description = "OpenSearch Serverless collection endpoint"
  type        = "String"
  value       = aws_opensearchserverless_collection.this.collection_endpoint

  tags = merge(var.tags, {
    Name = "${var.project_name}-opensearch-endpoint"
  })
}

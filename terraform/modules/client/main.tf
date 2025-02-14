resource "aws_security_group" "client" {
  name        = "${var.project_name}-client-sg"
  description = "Security group for client applications"
  vpc_id      = var.vpc_id

  egress {
    description = "Access to Neptune"
    from_port   = 8182
    to_port     = 8182
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Access to OpenSearch"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-client-sg"
  })
}

resource "aws_iam_role" "client" {
  name = "${var.project_name}-client-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = var.deploy_notebook ? ["lambda.amazonaws.com", "sagemaker.amazonaws.com"] : ["lambda.amazonaws.com"]
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-client-role"
  })
}

resource "aws_iam_role_policy_attachment" "client_bedrock_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

resource "aws_iam_role_policy_attachment" "client_logging_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

resource "aws_iam_role_policy_attachment" "client_s3_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

resource "aws_iam_role_policy_attachment" "client_sagemaker_access" {
  count      = var.deploy_notebook ? 1 : 0
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

resource "aws_iam_role_policy_attachment" "client_neptune_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/NeptuneFullAccess"
}

resource "aws_sagemaker_notebook_instance" "notebook" {
  count                = var.deploy_notebook ? 1 : 0
  name                 = "${var.project_name}-notebook"
  role_arn            = aws_iam_role.client.arn
  instance_type       = var.notebook_instance_type
  platform_identifier = "notebook-al2-v2"
  subnet_id           = var.public_subnet_ids[0]
  security_groups     = [aws_security_group.client.id]

  tags = merge(var.tags, {
    Name = "${var.project_name}-notebook"
  })
}

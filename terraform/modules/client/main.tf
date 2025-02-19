resource "aws_security_group" "client" {
  name        = "${var.project_name}-client-sg"
  description = "Security group for client applications"
  vpc_id      = var.vpc_id

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
          Service = ["ec2.amazonaws.com", "ecs-tasks.amazonaws.com", "lambda.amazonaws.com"]
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

resource "aws_iam_role_policy_attachment" "client_s3_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

resource "aws_iam_role_policy_attachment" "client_logging_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

resource "aws_iam_role_policy_attachment" "client_neptune_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/NeptuneFullAccess"
}

resource "aws_iam_role_policy_attachment" "client_ssm_access" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
}

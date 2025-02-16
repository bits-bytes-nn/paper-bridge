resource "aws_security_group" "bastion_host" {
  count       = var.deploy_bastion_host ? 1 : 0
  name        = "${var.project_name}-bastion-host-sg"
  description = "Security group for bastion host"
  vpc_id      = var.vpc_id

  ingress {
    description = "Allow SSH from allowed IPs"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ip_ranges
  }

  ingress {
    description = "Allow HTTPS from allowed IPs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_ip_ranges
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-bastion-host-sg"
  })
}

resource "aws_security_group" "app_client" {
  name        = "${var.project_name}-app-client-sg"
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
    Name = "${var.project_name}-app-client-sg"
  })
}

resource "aws_iam_role" "workload" {
  name = "${var.project_name}-workload-role"

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
    Name = "${var.project_name}-workload-role"
  })
}

resource "aws_iam_role_policy_attachment" "workload_bedrock_access" {
  role       = aws_iam_role.workload.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

resource "aws_iam_role_policy_attachment" "workload_s3_access" {
  role       = aws_iam_role.workload.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

resource "aws_iam_role_policy_attachment" "workload_logging_access" {
  role       = aws_iam_role.workload.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

resource "aws_iam_role_policy_attachment" "workload_neptune_access" {
  role       = aws_iam_role.workload.name
  policy_arn = "arn:aws:iam::aws:policy/NeptuneFullAccess"
}

resource "aws_iam_role_policy_attachment" "workload_ssm_access" {
  role       = aws_iam_role.workload.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
}

resource "aws_iam_instance_profile" "bastion_host" {
  count = var.deploy_bastion_host ? 1 : 0
  name  = "${var.project_name}-bastion-host-profile"
  role  = aws_iam_role.workload.name
}

resource "aws_instance" "bastion_host" {
  count                   = var.deploy_bastion_host ? 1 : 0
  ami                     = data.aws_ami.amazon_linux_2.id
  iam_instance_profile    = aws_iam_instance_profile.bastion_host[0].name
  instance_type           = "t3.micro"
  disable_api_termination = false
  monitoring             = false
  subnet_id              = var.public_subnet_ids[0]
  user_data              = file("${path.module}/user_data.sh")
  vpc_security_group_ids = [aws_security_group.bastion_host[0].id]

  root_block_device {
    volume_size = 8
    encrypted   = true
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-bastion-host"
  })
}

data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }
}

resource "aws_ssm_parameter" "bastion_host_instance_id" {
  count       = var.deploy_bastion_host ? 1 : 0
  name        = "/${var.project_name}/bastion-host/instance-id"
  description = "Bastion host instance ID"
  type        = "String"
  value       = aws_instance.bastion_host[0].id

  tags = merge(var.tags, {
    Name = "${var.project_name}-bastion-host-instance-id"
  })
}

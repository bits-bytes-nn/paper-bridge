data "aws_region" "current" {}

resource "aws_security_group" "client" {
  name        = "${var.project_name}-client"
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
    Name = "${var.project_name}-client"
  })
}

# IAM roles and policies
resource "aws_iam_role" "client" {
  name = "${var.project_name}-client"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "batch.amazonaws.com",
            "ec2.amazonaws.com",
            "ecs-tasks.amazonaws.com",
            "lambda.amazonaws.com",
            "sagemaker.amazonaws.com"
          ]
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-client"
  })
}

resource "aws_iam_instance_profile" "client" {
  name = "${var.project_name}-client"
  role = aws_iam_role.client.name
}

locals {
  client_policy_attachments = {
    bedrock    = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
    batch      = "arn:aws:iam::aws:policy/AWSBatchFullAccess"
    ecr        = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess"
    ec2        = "arn:aws:iam::aws:policy/AmazonEC2FullAccess"
    ecs        = "arn:aws:iam::aws:policy/AmazonECS_FullAccess"
    cloudwatch = "arn:aws:iam::aws:policy/CloudWatchFullAccess"
    neptune    = "arn:aws:iam::aws:policy/NeptuneFullAccess"
    s3         = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
    ssm        = "arn:aws:iam::aws:policy/AmazonSSMFullAccess"
  }

  # Common prefixes and settings
  batch_environment_prefix = "${var.project_name}-batch"
  ssm_param_prefix         = "/${var.project_name}"

  # Common compute resources for batch environments
  common_compute_resources = {
    security_group_ids = [aws_security_group.client.id]
    subnets            = var.private_subnet_ids
    instance_role      = aws_iam_instance_profile.client.arn
    instance_type      = ["optimal"]
    tags               = var.tags
  }

  # Source files for Docker builds with proper organization
  indexer_source_files = concat(
    tolist(fileset("${var.root_dir}/paper_bridge/indexer/configs", "**/*.{py,yaml}")),
    tolist(fileset("${var.root_dir}/paper_bridge/indexer/src", "**/*.py")),
    ["${var.root_dir}/paper_bridge/indexer/main.py"],
    ["${var.root_dir}/paper_bridge/indexer/Dockerfile"],
    ["${var.root_dir}/paper_bridge/indexer/requirements.txt"]
  )

  indexer_hash = md5(join("", [
    for f in local.indexer_source_files : fileexists("${f}") ? filemd5("${f}") : ""
  ]))

  cleaner_source_files = concat(
    tolist(fileset("${var.root_dir}/paper_bridge/cleaner/configs", "**/*.{py,yaml}")),
    tolist(fileset("${var.root_dir}/paper_bridge/cleaner/src", "**/*.py")),
    ["${var.root_dir}/paper_bridge/cleaner/main.py"],
    ["${var.root_dir}/paper_bridge/cleaner/Dockerfile"],
    ["${var.root_dir}/paper_bridge/cleaner/requirements.txt"]
  )

  cleaner_hash = md5(join("", [
    for f in local.cleaner_source_files : fileexists("${f}") ? filemd5("${f}") : ""
  ]))
}

resource "aws_iam_role_policy_attachment" "client_policies" {
  for_each   = local.client_policy_attachments
  role       = aws_iam_role.client.name
  policy_arn = each.value
}

resource "aws_iam_role" "bedrock_inference" {
  name = "${var.project_name}-bedrock-inference"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "bedrock.amazonaws.com"
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = merge(var.tags, {
    Name = "${var.project_name}-bedrock-inference"
  })
}

resource "aws_iam_role_policy_attachment" "s3_to_bedrock_inference" {
  role       = aws_iam_role.bedrock_inference.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

resource "aws_ssm_parameter" "bedrock_inference" {
  name  = "${local.ssm_param_prefix}/iam/bedrock-inference"
  type  = "String"
  value = aws_iam_role.bedrock_inference.name
  tags  = var.tags
}

# ECR Repositories
resource "aws_ecr_repository" "indexer" {
  name                 = "${var.project_name}-indexer"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}
resource "aws_ecr_repository" "cleaner" {
  name                 = "${var.project_name}-cleaner"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

# Docker build and push resources
resource "null_resource" "docker_build_push_indexer" {
  triggers = {
    content_hash = local.indexer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      export DOCKER_BUILDKIT=1
      aws ecr get-login-password --region ${data.aws_region.current.name} | docker login --username AWS --password-stdin ${aws_ecr_repository.indexer.repository_url}
      docker pull ${aws_ecr_repository.indexer.repository_url}:latest || true
      docker build \
        --platform linux/amd64 \
        --cache-from ${aws_ecr_repository.indexer.repository_url}:latest \
        -t ${aws_ecr_repository.indexer.repository_url}:latest \
        ${var.root_dir}/paper_bridge/indexer
      docker push ${aws_ecr_repository.indexer.repository_url}:latest
    EOF
  }

  depends_on = [aws_ecr_repository.indexer]
}

resource "null_resource" "docker_build_push_cleaner" {
  triggers = {
    content_hash = local.cleaner_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      export DOCKER_BUILDKIT=1
      aws ecr get-login-password --region ${data.aws_region.current.name} | docker login --username AWS --password-stdin ${aws_ecr_repository.cleaner.repository_url}
      docker pull ${aws_ecr_repository.cleaner.repository_url}:latest || true
      docker build \
        --platform linux/amd64 \
        --cache-from ${aws_ecr_repository.cleaner.repository_url}:latest \
        -t ${aws_ecr_repository.cleaner.repository_url}:latest \
        ${var.root_dir}/paper_bridge/cleaner
      docker push ${aws_ecr_repository.cleaner.repository_url}:latest
    EOF
  }

  depends_on = [aws_ecr_repository.cleaner]
}

# AWS Batch compute environments
resource "aws_batch_compute_environment" "ondemand" {
  compute_environment_name_prefix = "${local.batch_environment_prefix}-ondemand-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus     = 4
    min_vcpus     = 0
    type          = "EC2"

    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    instance_role       = local.common_compute_resources.instance_role
    instance_type       = local.common_compute_resources.instance_type

    security_group_ids = local.common_compute_resources.security_group_ids
    subnets            = local.common_compute_resources.subnets

    tags = local.common_compute_resources.tags
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_batch_compute_environment" "spot" {
  compute_environment_name_prefix = "${local.batch_environment_prefix}-spot-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus     = 8
    min_vcpus     = 0
    type          = "EC2"

    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    instance_role       = local.common_compute_resources.instance_role
    instance_type       = local.common_compute_resources.instance_type
    spot_iam_fleet_role = aws_iam_role.client.arn
    bid_percentage      = 100

    security_group_ids = local.common_compute_resources.security_group_ids
    subnets            = local.common_compute_resources.subnets

    tags = local.common_compute_resources.tags
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_batch_job_queue" "this" {
  name     = "${var.project_name}-job-queue"
  state    = "ENABLED"
  priority = 1

  compute_environments = [
    aws_batch_compute_environment.ondemand.arn,
    aws_batch_compute_environment.spot.arn
  ]

  tags = var.tags
}

# Secure parameters
resource "aws_ssm_parameter" "llama_cloud_api_key" {
  name        = "${local.ssm_param_prefix}/batch/llama-cloud-api-key"
  description = "API key for LLAMA Cloud services"
  type        = "SecureString"
  value       = var.llama_cloud_api_key
  tags        = var.tags
}

# Notification resources
resource "aws_sns_topic" "this" {
  name         = var.project_name
  display_name = "${var.project_name} Notifications"
  tags         = var.tags
}

resource "aws_sns_topic_subscription" "email_subscription" {
  count     = var.email_address != null ? 1 : 0
  topic_arn = aws_sns_topic.this.arn
  protocol  = "email"
  endpoint  = var.email_address
}

# Logging resources
resource "aws_cloudwatch_log_group" "indexer" {
  name              = "/aws/batch/${var.project_name}-indexer"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "cleaner" {
  name              = "/aws/lambda/${var.project_name}-cleaner"
  retention_in_days = 14
  tags              = var.tags
}

# Batch job definition
resource "aws_batch_job_definition" "indexer" {
  name = "${var.project_name}-indexer"
  type = "container"

  container_properties = jsonencode({
    image       = "${aws_ecr_repository.indexer.repository_url}:latest"
    command     = ["python3", "main.py", "--target-date", "Ref::target_date", "--days-to-fetch", "Ref::days_to_fetch", "--enable-batch-inference", "Ref::enable_batch_inference"]
    jobRoleArn  = aws_iam_role.client.arn

    resourceRequirements = [
      {
        type  = "VCPU"
        value = "4"
      },
      {
        type  = "MEMORY"
        value = "2048"
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.indexer.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "indexer"
      }
    }

    environment = [
      {
        name  = "LOG_LEVEL"
        value = "INFO"
      },
      {
        name  = "TOPIC_ARN"
        value = aws_sns_topic.this.arn
      }
    ]
  })

  retry_strategy {
    attempts = 3
  }

  timeout {
    attempt_duration_seconds = 10800
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.indexer]
}
# Lambda function for cleaner
resource "aws_lambda_function" "cleaner" {
  function_name = "${var.project_name}-cleaner"
  role          = aws_iam_role.client.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.cleaner.repository_url}:latest"
  memory_size   = 256
  timeout       = 300

  architectures = ["x86_64"]

  environment {
    variables = {
      LOG_LEVEL = "INFO"
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.client.id]
  }

  tags = var.tags

  depends_on = [
    null_resource.docker_build_push_cleaner,
    aws_cloudwatch_log_group.cleaner
  ]
}

# SSM Parameters for easy reference
resource "aws_ssm_parameter" "batch_job_queue" {
  name  = "${local.ssm_param_prefix}/batch/job-queue"
  type  = "String"
  value = aws_batch_job_queue.this.name
  tags  = var.tags
}

resource "aws_ssm_parameter" "batch_job_definition" {
  name  = "${local.ssm_param_prefix}/batch/job-definition"
  type  = "String"
  value = aws_batch_job_definition.indexer.name
  tags  = var.tags
}

# CloudWatch Event resources for scheduling
resource "aws_iam_role" "event_rule" {
  name = "${var.project_name}-event-rule"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "batch_to_event_rule" {
  role       = aws_iam_role.event_rule.name
  policy_arn = "arn:aws:iam::aws:policy/AWSBatchFullAccess"
}

# Using the correct Lambda policy ARN
resource "aws_iam_role_policy_attachment" "lambda_to_event_rule" {
  role       = aws_iam_role.event_rule.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaRole"
}

resource "aws_cloudwatch_event_rule" "indexer" {
  name                = "${var.project_name}-indexer"
  description         = "Schedule for running the indexer batch job"
  schedule_expression = var.indexer_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_rule" "cleaner" {
  name                = "${var.project_name}-cleaner"
  description         = "Schedule for running the cleaner lambda function"
  schedule_expression = var.cleaner_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "indexer" {
  rule     = aws_cloudwatch_event_rule.indexer.name
  arn      = aws_batch_job_queue.this.arn
  role_arn = aws_iam_role.event_rule.arn

  batch_target {
    job_definition = aws_batch_job_definition.indexer.arn
    job_name       = "${var.project_name}-indexer"
    array_size     = 2
    job_attempts   = 3
  }

  input = jsonencode({
    Parameters = {
      target_date            = "",
      days_to_fetch          = "0",
      enable_batch_inference = "false"
    }
  })
}

resource "aws_cloudwatch_event_target" "cleaner" {
  rule     = aws_cloudwatch_event_rule.cleaner.name
  arn      = aws_lambda_function.cleaner.arn
  role_arn = aws_iam_role.event_rule.arn
}

resource "aws_lambda_permission" "cloudwatch_to_cleaner" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cleaner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleaner.arn
}

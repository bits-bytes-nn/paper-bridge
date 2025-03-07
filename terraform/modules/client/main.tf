data "aws_region" "current" {}

locals {
  # Common prefixes and settings
  project_name              = var.project_name
  batch_environment_prefix  = "${local.project_name}-batch"
  ssm_param_prefix          = "/${local.project_name}"
  lambda_runtime            = "python3.10"

  # Common compute resources for batch environments
  common_compute_resources = {
    security_group_ids = [aws_security_group.client.id]
    subnets            = var.private_subnet_ids
    instance_role      = aws_iam_instance_profile.client.arn
    instance_type      = ["optimal"]
    tags               = var.tags
  }

  # Source files for Docker builds with more explicit file patterns
  indexer_source_files = concat(
    tolist(fileset("${var.root_dir}/paper_bridge/indexer/configs", "**/*.{py,yaml,yml,json}")),
    tolist(fileset("${var.root_dir}/paper_bridge/indexer/src", "**/*.py")),
    ["${var.root_dir}/paper_bridge/indexer/main.py"],
    ["${var.root_dir}/paper_bridge/indexer/Dockerfile"],
    ["${var.root_dir}/paper_bridge/indexer/requirements.txt"]
  )

  # More robust hash calculation with error handling
  indexer_hash = md5(join("", [
    for f in local.indexer_source_files : fileexists(f) ? filemd5(f) : ""
  ]))

  cleaner_source_files = concat(
    tolist(fileset("${var.root_dir}/paper_bridge/cleaner/configs", "**/*.{py,yaml,yml,json}")),
    tolist(fileset("${var.root_dir}/paper_bridge/cleaner/src", "**/*.py")),
    ["${var.root_dir}/paper_bridge/cleaner/main.py"],
    ["${var.root_dir}/paper_bridge/cleaner/Dockerfile"],
    ["${var.root_dir}/paper_bridge/cleaner/requirements.txt"]
  )

  cleaner_hash = md5(join("", [
    for f in local.cleaner_source_files : fileexists(f) ? filemd5(f) : ""
  ]))

  # IAM policy attachments map with more specific naming
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

  # Common tags with name
  common_name_tag = {
    Name = "${local.project_name}-client"
  }

  # Resource naming convention
  resource_names = {
    security_group       = "${local.project_name}-client"
    iam_role_client      = "${local.project_name}-client"
    iam_role_bedrock     = "${local.project_name}-bedrock-inference"
    ecr_repository       = "${local.project_name}-indexer"
    ecr_repository_cleaner = "${local.project_name}-cleaner"
    job_queue            = "${local.project_name}-job-queue"
    job_definition       = "${local.project_name}-indexer"
    lambda_function      = "${local.project_name}-cleaner"
    event_rule_role      = "${local.project_name}-event-rule"
    cloudwatch_indexer   = "${local.project_name}-indexer"
    cloudwatch_cleaner   = "${local.project_name}-cleaner"
  }

  # Default resource configurations
  default_configs = {
    lambda = {
      memory_size = 256
      timeout     = 300
    }
    batch_job = {
      vcpu        = 4
      memory      = 2048
      retry       = 3
      timeout     = 10800
    }
    log_retention = 14
  }
}

# Security Group
resource "aws_security_group" "client" {
  name        = local.resource_names.security_group
  description = "Security group for client applications"
  vpc_id      = var.vpc_id

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, local.common_name_tag)
}

# IAM roles and policies
resource "aws_iam_role" "client" {
  name = local.resource_names.iam_role_client

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

  tags = merge(var.tags, local.common_name_tag)
}

resource "aws_iam_instance_profile" "client" {
  name = local.resource_names.iam_role_client
  role = aws_iam_role.client.name
}

resource "aws_iam_role_policy_attachment" "client_policies" {
  for_each   = local.client_policy_attachments
  role       = aws_iam_role.client.name
  policy_arn = each.value
}

resource "aws_iam_role" "bedrock_inference" {
  name = local.resource_names.iam_role_bedrock

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
    Name = local.resource_names.iam_role_bedrock
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
  name                 = local.resource_names.ecr_repository
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_repository" "cleaner" {
  name                 = local.resource_names.ecr_repository_cleaner
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

# Docker build and push for indexer
resource "null_resource" "indexer" {
  triggers = {
    content_hash = local.indexer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      set -e
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

# CodeBuild용 IAM 역할 생성
resource "aws_iam_role" "codebuild" {
  name = "${local.project_name}-codebuild"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "codebuild.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

# CodeBuild에 필요한 정책 연결
resource "aws_iam_role_policy" "codebuild" {
  role = aws_iam_role.codebuild.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:PutObject"
        ]
        Resource = "*"
      }
    ]
  })
}

# S3 버킷 생성 (소스 코드 업로드용)
resource "aws_s3_bucket" "codebuild_source" {
  bucket = "${local.project_name}-codebuild-source"
  tags   = var.tags
}

# S3 버킷에 대한 명시적 정책
resource "aws_s3_bucket_policy" "codebuild_source" {
  bucket = aws_s3_bucket.codebuild_source.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = {
          AWS = aws_iam_role.codebuild.arn
        }
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.codebuild_source.arn}/*"
      }
    ]
  })
}

# 소스 코드 압축 및 S3 업로드 (cleaner)
resource "null_resource" "upload_cleaner_source" {
  triggers = {
    content_hash = local.cleaner_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      cd ${var.root_dir}
      zip -r cleaner_source.zip paper_bridge/cleaner
      aws s3 cp cleaner_source.zip s3://${aws_s3_bucket.codebuild_source.bucket}/cleaner_source.zip
    EOF
  }

  depends_on = [aws_s3_bucket.codebuild_source]
}

# CodeBuild 프로젝트 (cleaner)
resource "aws_codebuild_project" "cleaner" {
  name          = "${local.project_name}-cleaner-build"
  description   = "Builds Docker image for the cleaner Lambda function"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 15

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type                        = "LINUX_CONTAINER"
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/amazonlinux2-x86_64-standard:3.0"
    privileged_mode             = true # Docker 빌드를 위해 필요

    environment_variable {
      name  = "ECR_REPOSITORY_URI"
      value = aws_ecr_repository.cleaner.repository_url
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.name
    }
  }

  source {
  type      = "S3"
  location  = "${aws_s3_bucket.codebuild_source.bucket}/cleaner_source.zip"
  buildspec = <<EOF
version: 0.2

phases:
  pre_build:
    commands:
      - echo "Logging in to Amazon ECR..."
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_REPOSITORY_URI

  build:
    commands:
      - echo "Building the Docker image..."
      - cd paper_bridge/cleaner
      - ls -la
      - docker build -t $ECR_REPOSITORY_URI:latest .

  post_build:
    commands:
      - echo "Pushing the Docker image..."
      - docker push $ECR_REPOSITORY_URI:latest
      - echo "Build completed on $(date)"
EOF
}

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${local.project_name}-cleaner-build"
    }
  }

  tags = var.tags
}
# CodeBuild 트리거 및 완료 대기
resource "null_resource" "trigger_and_wait_cleaner_build" {
  triggers = {
    content_hash = local.cleaner_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      set -e
      # 빌드 시작
      BUILD_ID=$(aws codebuild start-build --project-name ${aws_codebuild_project.cleaner.name} --region ${data.aws_region.current.name} --query 'build.id' --output text)
      echo "Started build with ID: $BUILD_ID"

      # 폴링 방식으로 빌드 완료 대기
      echo "Waiting for build to complete..."
      STATUS="IN_PROGRESS"
      while [ "$STATUS" = "IN_PROGRESS" ]; do
        sleep 10
        STATUS=$(aws codebuild batch-get-builds --ids $BUILD_ID --region ${data.aws_region.current.name} --query 'builds[0].buildStatus' --output text)
        echo "Current build status: $STATUS"
      done

      # 빌드 상태 확인
      if [ "$STATUS" != "SUCCEEDED" ]; then
        echo "Build failed with status: $STATUS"
        exit 1
      fi

      echo "Build completed successfully"

      # 이미지 존재 확인 (추가 검증)
      sleep 5  # ECR에 이미지가 등록되는데 약간의 시간이 필요할 수 있음
      aws ecr describe-images --repository-name ${local.resource_names.ecr_repository_cleaner} --image-ids imageTag=latest --region ${data.aws_region.current.name}

      echo "ECR 이미지 확인 완료"
    EOF
  }

  depends_on = [
    aws_codebuild_project.cleaner,
    null_resource.upload_cleaner_source
  ]
}

# AWS Batch compute environments
resource "aws_batch_compute_environment" "ondemand" {
  compute_environment_name_prefix = "${local.batch_environment_prefix}-ondemand-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus = 4
    min_vcpus = 0
    type      = "EC2"

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
    max_vcpus = 8
    min_vcpus = 0
    type      = "EC2"

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
  name     = local.resource_names.job_queue
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
  name         = local.project_name
  display_name = "${local.project_name} Notifications"
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
  name              = "/aws/batch/${local.resource_names.job_definition}"
  retention_in_days = local.default_configs.log_retention
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "cleaner" {
  name              = "/aws/lambda/${local.resource_names.lambda_function}"
  retention_in_days = local.default_configs.log_retention
  tags              = var.tags
}

# Batch job definition
resource "aws_batch_job_definition" "indexer" {
  name = local.resource_names.job_definition
  type = "container"

  container_properties = jsonencode({
    image      = "${aws_ecr_repository.indexer.repository_url}:latest"
    command    = ["python3", "main.py", "--target-date", "Ref::target_date", "--days-to-fetch", "Ref::days_to_fetch", "--enable-batch-inference", "Ref::enable_batch_inference"]
    jobRoleArn = aws_iam_role.client.arn

    resourceRequirements = [
      {
        type  = "VCPU"
        value = tostring(local.default_configs.batch_job.vcpu)
      },
      {
        type  = "MEMORY"
        value = tostring(local.default_configs.batch_job.memory)
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
    attempts = local.default_configs.batch_job.retry
  }

  timeout {
    attempt_duration_seconds = local.default_configs.batch_job.timeout
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.indexer]
}

# Lambda function for cleaner
resource "aws_lambda_function" "cleaner" {
  function_name = local.resource_names.lambda_function
  role          = aws_iam_role.client.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.cleaner.repository_url}:latest"
  memory_size   = local.default_configs.lambda.memory_size
  timeout       = local.default_configs.lambda.timeout

  environment {
    variables = {
      LOG_LEVEL = "INFO"
      TOPIC_ARN = aws_sns_topic.this.arn
    }
  }

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.client.id]
  }

  tags = var.tags

  depends_on = [
    null_resource.trigger_and_wait_cleaner_build,
    aws_cloudwatch_log_group.cleaner
  ]
}

# SSM Parameters for reference
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
  name = local.resource_names.event_rule_role

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

resource "aws_iam_role_policy_attachment" "lambda_to_event_rule" {
  role       = aws_iam_role.event_rule.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaRole"
}

resource "aws_cloudwatch_event_rule" "indexer" {
  name                = local.resource_names.cloudwatch_indexer
  description         = "Schedule for running the indexer batch job"
  schedule_expression = var.indexer_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_rule" "cleaner" {
  name                = local.resource_names.cloudwatch_cleaner
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
    job_name       = "${local.project_name}-indexing-$${timestamp()}"
    array_size     = 2
    job_attempts   = local.default_configs.batch_job.retry
  }

  input = jsonencode({
    Parameters = {
      target_date            = "None",
      days_to_fetch          = "1",
      enable_batch_inference = "true"
    }
  })
}

resource "aws_cloudwatch_event_target" "cleaner" {
  rule     = aws_cloudwatch_event_rule.cleaner.name
  arn      = aws_lambda_function.cleaner.arn
}

resource "aws_lambda_permission" "cloudwatch_to_cleaner" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cleaner.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleaner.arn
}

# 디버깅용 출력 추가
output "ecr_repository_url" {
  value = aws_ecr_repository.cleaner.repository_url
  description = "ECR repository URL for the cleaner image"
}

output "codebuild_project_name" {
  value = aws_codebuild_project.cleaner.name
  description = "CodeBuild project name"
}

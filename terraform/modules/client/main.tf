data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix              = var.project_name
  batch_environment_prefix = "${local.name_prefix}-batch"
  # The application reads SSM parameters under "/{project_name}-{stage}" (see
  # Resources.stage in the app config, default "dev"), so the param names AND the
  # IAM scope derived from this prefix must include the stage suffix — otherwise
  # the Batch job role is denied ssm:GetParameter on /paper-bridge-dev/* while the
  # params live at /paper-bridge/*. Keep this in lockstep with config.yaml stage.
  ssm_param_prefix = "/${local.name_prefix}-${var.stage}"
  lambda_runtime   = "python3.10"

  # Identity/region values used to scope IAM policies to this account/region/project.
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # ARN scopes derived from the project naming convention. These keep IAM
  # permissions least-privilege without requiring new required variables.
  project_resource_arns = {
    # SSM parameters under the project path, e.g. /paper-bridge/*
    ssm_params = "arn:${local.partition}:ssm:${local.region}:${local.account_id}:parameter${local.ssm_param_prefix}/*"
    # ECR repositories prefixed with the project name, e.g. paper-bridge-indexer
    ecr_repos = "arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${local.name_prefix}-*"
    # CloudWatch log groups for the project's Batch jobs, Lambda, and CodeBuild
    log_groups = [
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/batch/${local.name_prefix}-*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/batch/${local.name_prefix}-*:*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-*:*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/codebuild/${local.name_prefix}-*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/codebuild/${local.name_prefix}-*:*",
    ]
    # Batch job queues and job definitions for this project
    batch_resources = [
      "arn:${local.partition}:batch:${local.region}:${local.account_id}:job-queue/${local.name_prefix}-*",
      "arn:${local.partition}:batch:${local.region}:${local.account_id}:job-definition/${local.name_prefix}-*",
      "arn:${local.partition}:batch:${local.region}:${local.account_id}:job/${local.name_prefix}-*",
    ]
    # Neptune cluster for this project
    neptune_cluster = "arn:${local.partition}:neptune-db:${local.region}:${local.account_id}:*/*"
    # S3: the CodeBuild source bucket (objects scoped to project prefix).
    # Use "${name_prefix}*" (not "${name_prefix}/*") so the scope covers BOTH the
    # build-source prefix "paper-bridge/" AND the stage-namespaced runtime output
    # prefix "paper-bridge-dev/outputs/..." that the summarizer writes to.
    # Without the stage suffix the summarizer's result upload gets AccessDenied.
    s3_source_bucket     = "arn:${local.partition}:s3:::${var.codebuild_source_bucket}"
    s3_source_bucket_obj = "arn:${local.partition}:s3:::${var.codebuild_source_bucket}/${local.name_prefix}*"
  }

  # Common compute resources for batch environments
  common_compute_resources = {
    subnets            = var.private_subnet_ids
    security_group_ids = [aws_security_group.client.id]
    instance_role      = aws_iam_instance_profile.client.arn
    instance_type      = ["optimal"]
    tags               = var.tags
  }

  # Source files for Docker builds with more explicit file patterns
  # The shared package is COPYied into every image, so it participates in each
  # subsystem's source hash (a shared/ change must trigger a rebuild). fileset
  # yields paths relative to its root, so prefix them back to absolute for filemd5.
  shared_source_files = [
    for f in tolist(fileset("${var.root_dir}/paper_bridge/shared", "**/*.py")) :
    "${var.root_dir}/paper_bridge/shared/${f}"
  ]

  indexer_source_files = concat(
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/indexer/configs", "**/*.{py,yaml,yml}")) : "${var.root_dir}/paper_bridge/indexer/configs/${f}"],
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/indexer/src", "**/*.py")) : "${var.root_dir}/paper_bridge/indexer/src/${f}"],
    local.shared_source_files,
    ["${var.root_dir}/paper_bridge/indexer/main.py"],
    ["${var.root_dir}/paper_bridge/indexer/Dockerfile"],
    ["${var.root_dir}/paper_bridge/indexer/requirements.txt"]
  )

  indexer_hash = md5(join("", [
    for f in local.indexer_source_files : fileexists(f) ? filemd5(f) : ""
  ]))

  cleaner_source_files = concat(
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/cleaner/configs", "**/*.{py,yaml,yml}")) : "${var.root_dir}/paper_bridge/cleaner/configs/${f}"],
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/cleaner/src", "**/*.py")) : "${var.root_dir}/paper_bridge/cleaner/src/${f}"],
    local.shared_source_files,
    ["${var.root_dir}/paper_bridge/cleaner/main.py"],
    ["${var.root_dir}/paper_bridge/cleaner/Dockerfile"],
    ["${var.root_dir}/paper_bridge/cleaner/requirements.txt"]
  )

  cleaner_hash = md5(join("", [
    for f in local.cleaner_source_files : fileexists(f) ? filemd5(f) : ""
  ]))

  summarizer_source_files = concat(
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/summarizer/configs", "**/*.{py,yaml,yml}")) : "${var.root_dir}/paper_bridge/summarizer/configs/${f}"],
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/summarizer/src", "**/*.py")) : "${var.root_dir}/paper_bridge/summarizer/src/${f}"],
    [for f in tolist(fileset("${var.root_dir}/paper_bridge/summarizer", "**/*.html")) : "${var.root_dir}/paper_bridge/summarizer/${f}"],
    local.shared_source_files,
    ["${var.root_dir}/paper_bridge/summarizer/main.py"],
    ["${var.root_dir}/paper_bridge/summarizer/Dockerfile"],
    ["${var.root_dir}/paper_bridge/summarizer/requirements.txt"]
  )

  summarizer_hash = md5(join("", [
    for f in local.summarizer_source_files : fileexists(f) ? filemd5(f) : ""
  ]))

  # Resource naming convention
  resource_names = {
    security_group            = "${local.name_prefix}-client"
    iam_role_client           = "${local.name_prefix}-client"
    iam_role_bedrock          = "${local.name_prefix}-bedrock-inference"
    iam_role_codebuild        = "${local.name_prefix}-codebuild"
    iam_role_event_rule       = "${local.name_prefix}-event"
    ecr_repository_indexer    = "${local.name_prefix}-indexer"
    ecr_repository_cleaner    = "${local.name_prefix}-cleaner"
    ecr_repository_summarizer = "${local.name_prefix}-summarizer"
    job_queue_indexer         = "${local.name_prefix}-indexer"
    job_queue_summarizer      = "${local.name_prefix}-summarizer"
    job_definition_indexer    = "${local.name_prefix}-indexer"
    job_definition_summarizer = "${local.name_prefix}-summarizer"
    lambda_function_cleaner   = "${local.name_prefix}-cleaner"
    cloudwatch_indexer        = "${local.name_prefix}-indexer"
    cloudwatch_cleaner        = "${local.name_prefix}-cleaner"
    cloudwatch_summarizer     = "${local.name_prefix}-summarizer"
    codebuild_indexer         = "${local.name_prefix}-indexer"
    codebuild_cleaner         = "${local.name_prefix}-cleaner"
    codebuild_summarizer      = "${local.name_prefix}-summarizer"
    sns_topic                 = local.name_prefix
  }

  # Default resource configurations
  default_configs = {
    indexer_batch = {
      # 8192 MB fits a single m5.xlarge (4 vCPU / 16 GB) under the "optimal"
      # compute environment (maxvCpus = 4). An earlier 16384 bump was made on an
      # OOM hypothesis that turned out to be wrong — the real E2E failure was an
      # ssm:GetParameter AccessDenied (stage-prefix mismatch, since fixed), which
      # crashed the job in ~9s before GraphRAG ever loaded. With no evidence of an
      # actual OOM, this stays at the original 8192; revisit only if a real run
      # gets SIGKILLed during run_extract_and_build.
      vcpu    = 4
      memory  = 8192
      retry   = 2
      timeout = 10800
    }
    summarizer_batch = {
      vcpu    = 2
      memory  = 4096
      retry   = 2
      timeout = 10800
    }

    cleaner_lambda = {
      memory_size = 256
      timeout     = 300
    }

    codebuild = {
      timeout      = 30
      compute_type = "BUILD_GENERAL1_SMALL"
      image        = "aws/codebuild/amazonlinux2-x86_64-standard:3.0"
    }
    log_retention = 14
    batch_compute = {
      ondemand_max_vcpus = 4
      spot_max_vcpus     = 8
      min_vcpus          = 0
      bid_percentage     = 100
    }
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

  tags = var.tags
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
            "sagemaker.amazonaws.com",
            "spotfleet.amazonaws.com"
          ]
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_instance_profile" "client" {
  name = local.resource_names.iam_role_client
  role = aws_iam_role.client.name
}

# Least-privilege policy for the multi-purpose client role. This role is used as
# the Batch job role, Lambda execution role, ECS task role, EC2 instance profile,
# and Spot fleet role. Permissions are scoped to this project's resources where an
# ARN can be derived; the few genuinely account/region-wide describe/list calls
# (which do not support resource-level scoping) are isolated and commented below.
data "aws_iam_policy_document" "client" {
  # --- Bedrock model invocation ---
  # Model ARNs (foundation models / inference profiles) are dynamic and live in
  # var.bedrock_region_name; scope the actions but allow any model resource.
  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:CreateModelInvocationJob",
      "bedrock:GetModelInvocationJob",
      "bedrock:StopModelInvocationJob",
      "bedrock:ListFoundationModels",
      "bedrock:GetFoundationModel",
      # The app resolves cross-region inference-profile IDs at runtime
      # (get_cross_inference_model_id -> list_inference_profiles); without these
      # the indexer/summarizer log "Error checking cross-inference support".
      "bedrock:ListInferenceProfiles",
      "bedrock:GetInferenceProfile",
    ]
    resources = ["*"] # Bedrock model/inference-profile ARNs are dynamic; action-scoped only.
  }

  # --- S3: CodeBuild source bucket (project prefix only) ---
  statement {
    sid    = "S3Objects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [local.project_resource_arns.s3_source_bucket_obj]
  }

  statement {
    sid    = "S3ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [local.project_resource_arns.s3_source_bucket]
  }

  # --- SSM: project parameters only ---
  statement {
    sid    = "SSMParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [local.project_resource_arns.ssm_params]
  }

  # --- ECR: pull project images (auth token is account-wide by design) ---
  statement {
    sid       = "ECRAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken does not support resource-level scoping.
  }

  statement {
    sid    = "ECRPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]
    resources = [local.project_resource_arns.ecr_repos]
  }

  # --- AWS Batch: submit/describe project jobs ---
  statement {
    sid    = "BatchSubmit"
    effect = "Allow"
    actions = [
      "batch:SubmitJob",
      "batch:TerminateJob",
      "batch:CancelJob",
    ]
    resources = local.project_resource_arns.batch_resources
  }

  statement {
    sid    = "BatchDescribe"
    effect = "Allow"
    actions = [
      "batch:DescribeJobs",
      "batch:DescribeJobQueues",
      "batch:DescribeJobDefinitions",
      "batch:DescribeComputeEnvironments",
      "batch:ListJobs",
    ]
    resources = ["*"] # Batch describe/list APIs do not support resource-level scoping.
  }

  # --- Neptune: connect to the cluster ---
  statement {
    sid    = "NeptuneConnect"
    effect = "Allow"
    actions = [
      "neptune-db:connect",
      "neptune-db:ReadDataViaQuery",
      "neptune-db:WriteDataViaQuery",
      "neptune-db:DeleteDataViaQuery",
      "neptune-db:GetQueryStatus",
      "neptune-db:CancelQuery",
    ]
    resources = [local.project_resource_arns.neptune_cluster]
  }

  # --- CloudWatch Logs: project log groups ---
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = local.project_resource_arns.log_groups
  }

  # --- SNS: publish to the project topic ---
  statement {
    sid       = "SNSPublish"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.this.arn]
  }

  # --- EC2 ENI management for the VPC-attached cleaner Lambda ---
  # These ENI lifecycle calls do not support resource-level scoping; required so
  # the Lambda can attach to the private subnets. Replaces former AmazonEC2FullAccess.
  statement {
    sid    = "LambdaVpcEni"
    effect = "Allow"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"] # EC2 ENI APIs do not support resource-level permissions.
  }
}

resource "aws_iam_role_policy" "client_scoped" {
  name   = "${local.name_prefix}-client-scoped"
  role   = aws_iam_role.client.name
  policy = data.aws_iam_policy_document.client.json
}

# Batch on EC2 launches container instances that must register with ECS. This is
# the AWS-managed role purpose-built for ECS-on-EC2 container instances; its
# actions (ECS register/poll, ECR pull, logs) are the standard, minimal set and
# several of them do not support resource-level scoping. Kept as-is by design.
resource "aws_iam_role_policy_attachment" "client_ecs_instance" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# This role is also used as the Spot fleet role for Batch Spot compute
# environments (see spot_iam_fleet_role). The AWS-managed Spot fleet tagging role
# is the minimal, purpose-built policy for requesting/tagging Spot instances;
# replaces the former broad AmazonEC2FullAccess attachment.
resource "aws_iam_role_policy_attachment" "client_spot_fleet" {
  role       = aws_iam_role.client.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
}

resource "aws_iam_role_policy" "client_pass_role_policy" {
  name = "${local.name_prefix}-client-pass-policy"
  role = aws_iam_role.client.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.bedrock_inference.arn
      }
    ]
  })
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

  tags = var.tags
}

# Bedrock batch inference reads inputs from / writes outputs to the project's S3
# bucket. Scoped to that bucket (project prefix) instead of AmazonS3FullAccess.
data "aws_iam_policy_document" "bedrock_inference_s3" {
  statement {
    sid    = "S3Objects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [local.project_resource_arns.s3_source_bucket_obj]
  }

  statement {
    sid    = "S3ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [local.project_resource_arns.s3_source_bucket]
  }
}

resource "aws_iam_role_policy" "s3_to_bedrock_inference" {
  name   = "${local.name_prefix}-bedrock-inference-s3"
  role   = aws_iam_role.bedrock_inference.name
  policy = data.aws_iam_policy_document.bedrock_inference_s3.json
}

resource "aws_ssm_parameter" "bedrock_inference" {
  name  = "${local.ssm_param_prefix}/iam-bedrock-inference"
  type  = "String"
  value = aws_iam_role.bedrock_inference.name
  tags  = var.tags
}

# ECR Repositories
resource "aws_ecr_repository" "indexer" {
  count = var.use_graph_rag ? 1 : 0
  name  = local.resource_names.ecr_repository_indexer
  # force_delete so `terraform destroy` can remove the repo even when it still
  # holds pushed images (otherwise destroy fails with RepositoryNotEmptyException).
  force_delete         = true
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_repository" "cleaner" {
  count                = var.use_graph_rag ? 1 : 0
  name                 = local.resource_names.ecr_repository_cleaner
  force_delete         = true
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

resource "aws_ecr_repository" "summarizer" {
  name                 = local.resource_names.ecr_repository_summarizer
  force_delete         = true
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

# ECR Lifecycle Policies - keep only the most recent images
resource "aws_ecr_lifecycle_policy" "indexer" {
  count      = var.use_graph_rag ? 1 : 0
  repository = aws_ecr_repository.indexer[0].name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 5 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "cleaner" {
  count      = var.use_graph_rag ? 1 : 0
  repository = aws_ecr_repository.cleaner[0].name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 5 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "summarizer" {
  repository = aws_ecr_repository.summarizer.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 5 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_iam_role" "codebuild" {
  name = local.resource_names.iam_role_codebuild

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "codebuild.amazonaws.com"
        }
        Action = ["sts:AssumeRole"]
      }
    ]
  })

  tags = var.tags
}

# Least-privilege policy for the CodeBuild role: pull/push project ECR images,
# read the project source from S3, and write to the project's CodeBuild log groups.
data "aws_iam_policy_document" "codebuild" {
  statement {
    sid       = "ECRAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken does not support resource-level scoping.
  }

  statement {
    sid    = "ECRPushPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [local.project_resource_arns.ecr_repos]
  }

  statement {
    sid       = "S3Source"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [local.project_resource_arns.s3_source_bucket_obj]
  }

  statement {
    sid       = "S3ListSource"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.project_resource_arns.s3_source_bucket]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/codebuild/${local.name_prefix}-*",
      "arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:/aws/codebuild/${local.name_prefix}-*:*",
    ]
  }
}

resource "aws_iam_role_policy" "codebuild_scoped" {
  name   = "${local.name_prefix}-codebuild-scoped"
  role   = aws_iam_role.codebuild.name
  policy = data.aws_iam_policy_document.codebuild.json
}

resource "null_resource" "upload_indexer_source" {
  count = var.use_graph_rag ? 1 : 0
  triggers = {
    content_hash = local.indexer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      cd ${var.root_dir}
      find paper_bridge/indexer paper_bridge/shared -type f \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.txt" -o -name "Dockerfile" \) | zip indexer_source.zip -@
      aws s3 cp indexer_source.zip s3://${var.codebuild_source_bucket}/${local.name_prefix}/indexer_source.zip
    EOF
  }
}

resource "null_resource" "upload_cleaner_source" {
  count = var.use_graph_rag ? 1 : 0
  triggers = {
    content_hash = local.cleaner_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      cd ${var.root_dir}
      find paper_bridge/cleaner paper_bridge/shared -type f \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.txt" -o -name "Dockerfile" \) | zip cleaner_source.zip -@
      aws s3 cp cleaner_source.zip s3://${var.codebuild_source_bucket}/${local.name_prefix}/cleaner_source.zip
    EOF
  }
}

resource "null_resource" "upload_summarizer_source" {
  triggers = {
    content_hash = local.summarizer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      cd ${var.root_dir}
      find paper_bridge/summarizer paper_bridge/shared -type f \( -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.txt" -o -name "*.html" -o -name "Dockerfile" \) | zip summarizer_source.zip -@
      aws s3 cp summarizer_source.zip s3://${var.codebuild_source_bucket}/${local.name_prefix}/summarizer_source.zip
    EOF
  }
}

resource "aws_codebuild_project" "indexer" {
  count         = var.use_graph_rag ? 1 : 0
  name          = local.resource_names.codebuild_indexer
  description   = "Builds Docker image for the indexer batch job"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = local.default_configs.codebuild.timeout

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = local.default_configs.codebuild.compute_type
    image           = local.default_configs.codebuild.image
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPOSITORY_URI"
      value = aws_ecr_repository.indexer[0].repository_url
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.name
    }
  }

  source {
    type      = "S3"
    location  = "${var.codebuild_source_bucket}/${local.name_prefix}/indexer_source.zip"
    buildspec = file("${var.root_dir}/scripts/indexer_buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${local.resource_names.codebuild_indexer}"
    }
  }

  tags = var.tags
}

resource "aws_codebuild_project" "cleaner" {
  count         = var.use_graph_rag ? 1 : 0
  name          = local.resource_names.codebuild_cleaner
  description   = "Builds Docker image for the cleaner Lambda function"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = local.default_configs.codebuild.timeout

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = local.default_configs.codebuild.compute_type
    image           = local.default_configs.codebuild.image
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPOSITORY_URI"
      value = aws_ecr_repository.cleaner[0].repository_url
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.name
    }
  }

  source {
    type      = "S3"
    location  = "${var.codebuild_source_bucket}/${local.name_prefix}/cleaner_source.zip"
    buildspec = file("${var.root_dir}/scripts/cleaner_buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${local.resource_names.codebuild_cleaner}"
    }
  }

  tags = var.tags
}

resource "aws_codebuild_project" "summarizer" {
  name          = local.resource_names.codebuild_summarizer
  description   = "Builds Docker image for the summarizer batch job"
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = local.default_configs.codebuild.timeout

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = local.default_configs.codebuild.compute_type
    image           = local.default_configs.codebuild.image
    privileged_mode = true

    environment_variable {
      name  = "ECR_REPOSITORY_URI"
      value = aws_ecr_repository.summarizer.repository_url
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = data.aws_region.current.name
    }
  }

  source {
    type      = "S3"
    location  = "${var.codebuild_source_bucket}/${local.name_prefix}/summarizer_source.zip"
    buildspec = file("${var.root_dir}/scripts/summarizer_buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/aws/codebuild/${local.resource_names.codebuild_summarizer}"
    }
  }

  tags = var.tags
}

resource "null_resource" "trigger_and_wait_indexer_build" {
  count = var.use_graph_rag ? 1 : 0
  triggers = {
    content_hash = local.indexer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      set -e
      # Retry once on transient IAM-propagation failure (see cleaner trigger note).
      ATTEMPT=0
      MAX_ATTEMPTS=2
      while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
        ATTEMPT=$((ATTEMPT + 1))
        BUILD_ID=$(aws codebuild start-build --project-name ${aws_codebuild_project.indexer[0].name} --region ${data.aws_region.current.name} --query 'build.id' --output text)
        echo "Attempt $ATTEMPT: started build $BUILD_ID"
        STATUS="IN_PROGRESS"
        while [ "$STATUS" = "IN_PROGRESS" ]; do
          sleep 10
          STATUS=$(aws codebuild batch-get-builds --ids $BUILD_ID --region ${data.aws_region.current.name} --query 'builds[0].buildStatus' --output text)
          echo "Current build status: $STATUS"
        done
        if [ "$STATUS" = "SUCCEEDED" ]; then
          break
        fi
        echo "Build failed with status: $STATUS (attempt $ATTEMPT/$MAX_ATTEMPTS)"
        if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
          echo "Waiting 30s for IAM propagation before retry..."
          sleep 30
        else
          exit 1
        fi
      done

      echo "Build completed successfully"

      # Verify image exists
      sleep 5  # Allow time for ECR image registration
      aws ecr describe-images --repository-name ${local.resource_names.ecr_repository_indexer} --image-ids imageTag=latest --region ${data.aws_region.current.name}

      echo "ECR image verification complete"

      # Clean up the zip file
      echo "Cleaning up source zip file..."
      rm -f ${var.root_dir}/indexer_source.zip
      aws s3 rm s3://${var.codebuild_source_bucket}/${local.name_prefix}/indexer_source.zip
      echo "Source zip file removed"
    EOF
  }

  depends_on = [
    aws_codebuild_project.indexer,
    aws_iam_role_policy.codebuild_scoped,
    null_resource.upload_indexer_source
  ]
}

resource "null_resource" "trigger_and_wait_cleaner_build" {
  count = var.use_graph_rag ? 1 : 0
  triggers = {
    content_hash = local.cleaner_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      set -e
      # Run the build, retrying once: the FIRST CodeBuild run of a fresh apply can
      # hit an IAM eventual-consistency error (the codebuild role's logs:CreateLogStream
      # permission may not have propagated yet), which surfaces as a build FAILED in
      # the QUEUED phase. A short wait + one retry clears it.
      ATTEMPT=0
      MAX_ATTEMPTS=2
      while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
        ATTEMPT=$((ATTEMPT + 1))
        BUILD_ID=$(aws codebuild start-build --project-name ${aws_codebuild_project.cleaner[0].name} --region ${data.aws_region.current.name} --query 'build.id' --output text)
        echo "Attempt $ATTEMPT: started build $BUILD_ID"
        STATUS="IN_PROGRESS"
        while [ "$STATUS" = "IN_PROGRESS" ]; do
          sleep 10
          STATUS=$(aws codebuild batch-get-builds --ids $BUILD_ID --region ${data.aws_region.current.name} --query 'builds[0].buildStatus' --output text)
          echo "Current build status: $STATUS"
        done
        if [ "$STATUS" = "SUCCEEDED" ]; then
          break
        fi
        echo "Build failed with status: $STATUS (attempt $ATTEMPT/$MAX_ATTEMPTS)"
        if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
          echo "Waiting 30s for IAM propagation before retry..."
          sleep 30
        else
          exit 1
        fi
      done

      echo "Build completed successfully"

      # Verify image exists
      sleep 5  # Allow time for ECR image registration
      aws ecr describe-images --repository-name ${local.resource_names.ecr_repository_cleaner} --image-ids imageTag=latest --region ${data.aws_region.current.name}

      echo "ECR image verification complete"

      # Clean up the zip file
      echo "Cleaning up source zip file..."
      rm -f ${var.root_dir}/cleaner_source.zip
      aws s3 rm s3://${var.codebuild_source_bucket}/${local.name_prefix}/cleaner_source.zip
      echo "Source zip file removed"
    EOF
  }

  depends_on = [
    aws_codebuild_project.cleaner,
    aws_iam_role_policy.codebuild_scoped,
    null_resource.upload_cleaner_source
  ]
}

resource "null_resource" "trigger_and_wait_summarizer_build" {
  triggers = {
    content_hash = local.summarizer_hash
  }

  provisioner "local-exec" {
    command = <<EOF
      set -e
      # Retry once on transient IAM-propagation failure (see cleaner trigger note).
      ATTEMPT=0
      MAX_ATTEMPTS=2
      while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
        ATTEMPT=$((ATTEMPT + 1))
        BUILD_ID=$(aws codebuild start-build --project-name ${aws_codebuild_project.summarizer.name} --region ${data.aws_region.current.name} --query 'build.id' --output text)
        echo "Attempt $ATTEMPT: started build $BUILD_ID"
        STATUS="IN_PROGRESS"
        while [ "$STATUS" = "IN_PROGRESS" ]; do
          sleep 10
          STATUS=$(aws codebuild batch-get-builds --ids $BUILD_ID --region ${data.aws_region.current.name} --query 'builds[0].buildStatus' --output text)
          echo "Current build status: $STATUS"
        done
        if [ "$STATUS" = "SUCCEEDED" ]; then
          break
        fi
        echo "Build failed with status: $STATUS (attempt $ATTEMPT/$MAX_ATTEMPTS)"
        if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
          echo "Waiting 30s for IAM propagation before retry..."
          sleep 30
        else
          exit 1
        fi
      done

      echo "Build completed successfully"

      # Verify image exists
      sleep 5  # Allow time for ECR image registration
      aws ecr describe-images --repository-name ${local.resource_names.ecr_repository_summarizer} --image-ids imageTag=latest --region ${data.aws_region.current.name}

      echo "ECR image verification complete"

      # Clean up the zip file
      echo "Cleaning up source zip file..."
      rm -f ${var.root_dir}/summarizer_source.zip
      aws s3 rm s3://${var.codebuild_source_bucket}/${local.name_prefix}/summarizer_source.zip
      echo "Source zip file removed"
    EOF
  }

  depends_on = [
    aws_codebuild_project.summarizer,
    aws_iam_role_policy.codebuild_scoped,
    null_resource.upload_summarizer_source
  ]
}
# AWS Batch compute environments
resource "aws_batch_compute_environment" "indexer_ondemand" {
  count                           = var.use_graph_rag ? 1 : 0
  compute_environment_name_prefix = "${local.batch_environment_prefix}-indexer-ondemand-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus = local.default_configs.batch_compute.ondemand_max_vcpus
    min_vcpus = local.default_configs.batch_compute.min_vcpus
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

resource "aws_batch_compute_environment" "indexer_spot" {
  count                           = var.use_graph_rag ? 1 : 0
  compute_environment_name_prefix = "${local.batch_environment_prefix}-indexer-spot-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus = local.default_configs.batch_compute.spot_max_vcpus
    min_vcpus = local.default_configs.batch_compute.min_vcpus
    type      = "EC2"

    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    instance_role       = local.common_compute_resources.instance_role
    instance_type       = local.common_compute_resources.instance_type
    spot_iam_fleet_role = aws_iam_role.client.arn
    bid_percentage      = local.default_configs.batch_compute.bid_percentage

    security_group_ids = local.common_compute_resources.security_group_ids
    subnets            = local.common_compute_resources.subnets

    tags = local.common_compute_resources.tags
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

# Batch compute environments for summarizer
resource "aws_batch_compute_environment" "summarizer_ondemand" {
  compute_environment_name_prefix = "${local.batch_environment_prefix}-summarizer-ondemand-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus = local.default_configs.batch_compute.ondemand_max_vcpus
    min_vcpus = local.default_configs.batch_compute.min_vcpus
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

resource "aws_batch_compute_environment" "summarizer_spot" {
  compute_environment_name_prefix = "${local.batch_environment_prefix}-summarizer-spot-"
  type                            = "MANAGED"
  state                           = "ENABLED"

  compute_resources {
    max_vcpus = local.default_configs.batch_compute.spot_max_vcpus
    min_vcpus = local.default_configs.batch_compute.min_vcpus
    type      = "EC2"

    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    instance_role       = local.common_compute_resources.instance_role
    instance_type       = local.common_compute_resources.instance_type
    spot_iam_fleet_role = aws_iam_role.client.arn
    bid_percentage      = local.default_configs.batch_compute.bid_percentage

    security_group_ids = local.common_compute_resources.security_group_ids
    subnets            = local.common_compute_resources.subnets

    tags = local.common_compute_resources.tags
  }

  tags = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_batch_job_queue" "indexer" {
  count    = var.use_graph_rag ? 1 : 0
  name     = local.resource_names.job_queue_indexer
  state    = "ENABLED"
  priority = 1

  compute_environments = [
    aws_batch_compute_environment.indexer_ondemand[0].arn,
    aws_batch_compute_environment.indexer_spot[0].arn
  ]

  tags = var.tags
}

resource "aws_batch_job_queue" "summarizer" {
  name     = local.resource_names.job_queue_summarizer
  state    = "ENABLED"
  priority = 1

  compute_environments = [
    aws_batch_compute_environment.summarizer_ondemand.arn,
    aws_batch_compute_environment.summarizer_spot.arn
  ]

  tags = var.tags
}

# Secure parameters

resource "aws_ssm_parameter" "business_slack_bot_token" {
  count       = var.business_slack_bot_token != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/business-slack-bot-token"
  description = "Slack bot token"
  type        = "SecureString"
  value       = var.business_slack_bot_token
  tags        = var.tags
}

resource "aws_ssm_parameter" "business_slack_channel_id" {
  count       = var.business_slack_channel_id != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/business-slack-channel-id"
  description = "Slack channel ID"
  type        = "SecureString"
  value       = var.business_slack_channel_id
  tags        = var.tags
}

resource "aws_ssm_parameter" "llama_cloud_api_key" {
  count       = var.llama_cloud_api_key != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/llama-cloud-api-key"
  description = "API key for LLAMA Cloud services"
  type        = "SecureString"
  value       = var.llama_cloud_api_key
  tags        = var.tags
}

resource "aws_ssm_parameter" "personal_slack_bot_token" {
  count       = var.personal_slack_bot_token != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/personal-slack-bot-token"
  description = "Slack bot token"
  type        = "SecureString"
  value       = var.personal_slack_bot_token
  tags        = var.tags
}

resource "aws_ssm_parameter" "personal_slack_channel_id" {
  count       = var.personal_slack_channel_id != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/personal-slack-channel-id"
  description = "Slack channel ID"
  type        = "SecureString"
  value       = var.personal_slack_channel_id
  tags        = var.tags
}

resource "aws_ssm_parameter" "upstage_api_key" {
  count       = var.upstage_api_key != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/upstage-api-key"
  description = "Upstage API key"
  type        = "SecureString"
  value       = var.upstage_api_key
  tags        = var.tags
}

resource "aws_ssm_parameter" "github_token" {
  count       = var.github_token != null ? 1 : 0
  name        = "${local.ssm_param_prefix}/github-token"
  description = "GitHub token for the summarizer's paper-summary PR workflow"
  type        = "SecureString"
  value       = var.github_token
  tags        = var.tags
}

# Notification resources
resource "aws_sns_topic" "this" {
  name         = local.resource_names.sns_topic
  display_name = "${local.name_prefix} Notifications"
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
  count             = var.use_graph_rag ? 1 : 0
  name              = "/aws/batch/${local.resource_names.job_definition_indexer}"
  retention_in_days = local.default_configs.log_retention
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "cleaner" {
  count             = var.use_graph_rag ? 1 : 0
  name              = "/aws/lambda/${local.resource_names.lambda_function_cleaner}"
  retention_in_days = local.default_configs.log_retention
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "summarizer" {
  name              = "/aws/batch/${local.resource_names.job_definition_summarizer}"
  retention_in_days = local.default_configs.log_retention
  tags              = var.tags
}

# Batch job definition
resource "aws_batch_job_definition" "indexer" {
  count = var.use_graph_rag ? 1 : 0
  name  = local.resource_names.job_definition_indexer
  type  = "container"
  container_properties = jsonencode({
    image      = "${aws_ecr_repository.indexer[0].repository_url}:latest"
    command    = ["python3", "main.py", "--target-date", "Ref::target_date", "--days-to-fetch", "Ref::days_to_fetch", "--arxiv-ids", "Ref::arxiv_ids"]
    jobRoleArn = aws_iam_role.client.arn

    resourceRequirements = [
      {
        type  = "VCPU"
        value = tostring(local.default_configs.indexer_batch.vcpu)
      },
      {
        type  = "MEMORY"
        value = tostring(local.default_configs.indexer_batch.memory)
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.indexer[0].name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "indexer"
      }
    }

    environment = [
      {
        name  = "AWS_DEFAULT_REGION"
        value = data.aws_region.current.name
      },
      {
        name  = "LOG_LEVEL"
        value = "INFO"
      },
      {
        name  = "TOPIC_ARN"
        value = aws_sns_topic.this.arn
      },
      {
        name  = "S3_BUCKET_NAME"
        value = var.codebuild_source_bucket
      },
      {
        name  = "IMAGE_VERSION"
        value = local.indexer_hash
      }
    ]
  })

  retry_strategy {
    attempts = local.default_configs.indexer_batch.retry
  }

  timeout {
    attempt_duration_seconds = local.default_configs.indexer_batch.timeout
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.indexer]
}

# Batch job definition for summarizer
resource "aws_batch_job_definition" "summarizer" {
  name = local.resource_names.job_definition_summarizer
  type = "container"
  container_properties = jsonencode({
    image      = "${aws_ecr_repository.summarizer.repository_url}:latest"
    command    = ["python3", "main.py", "--target-date", "Ref::target_date", "--days-to-fetch", "Ref::days_to_fetch", "--arxiv-ids", "Ref::arxiv_ids", "--language", "Ref::language", "--apply-retrieval", "Ref::apply_retrieval", "--send-business-slack", "Ref::send_business_slack"]
    jobRoleArn = aws_iam_role.client.arn

    resourceRequirements = [
      {
        type  = "VCPU"
        value = tostring(local.default_configs.summarizer_batch.vcpu)
      },
      {
        type  = "MEMORY"
        value = tostring(local.default_configs.summarizer_batch.memory)
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.summarizer.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "summarizer"
      }
    }

    environment = concat([
      {
        name  = "AWS_DEFAULT_REGION"
        value = data.aws_region.current.name
      },
      {
        name  = "LOG_LEVEL"
        value = "INFO"
      },
      {
        name  = "TOPIC_ARN"
        value = aws_sns_topic.this.arn
      },
      {
        name  = "S3_BUCKET_NAME"
        value = var.codebuild_source_bucket
      },
      {
        name  = "IMAGE_VERSION"
        value = local.summarizer_hash
      }
      ],
      var.github_repo_name != null ? [{
        name  = "GITHUB_REPO_NAME"
        value = var.github_repo_name
    }] : [])
  })

  retry_strategy {
    attempts = local.default_configs.summarizer_batch.retry
  }

  timeout {
    attempt_duration_seconds = local.default_configs.summarizer_batch.timeout
  }

  tags = var.tags

  depends_on = [
    null_resource.trigger_and_wait_summarizer_build,
    aws_cloudwatch_log_group.summarizer
  ]
}

# Lambda function for cleaner
resource "aws_lambda_function" "cleaner" {
  count         = var.use_graph_rag ? 1 : 0
  function_name = local.resource_names.lambda_function_cleaner
  role          = aws_iam_role.client.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.cleaner[0].repository_url}:latest"
  memory_size   = local.default_configs.cleaner_lambda.memory_size
  timeout       = local.default_configs.cleaner_lambda.timeout

  source_code_hash = local.cleaner_hash

  environment {
    variables = {
      DEFAULT_REGION_NAME = data.aws_region.current.name
      LOG_LEVEL           = "INFO"
      TOPIC_ARN           = aws_sns_topic.this.arn
      IMAGE_VERSION       = local.cleaner_hash
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
resource "aws_ssm_parameter" "batch_job_queue_indexer" {
  count = var.use_graph_rag ? 1 : 0
  name  = "${local.ssm_param_prefix}/batch-job-queue-indexer"
  type  = "String"
  value = aws_batch_job_queue.indexer[0].name
  tags  = var.tags
}

resource "aws_ssm_parameter" "batch_job_definition_indexer" {
  count = var.use_graph_rag ? 1 : 0
  name  = "${local.ssm_param_prefix}/batch-job-definition-indexer"
  type  = "String"
  value = aws_batch_job_definition.indexer[0].name
  tags  = var.tags
}

resource "aws_ssm_parameter" "batch_job_queue_summarizer" {
  name  = "${local.ssm_param_prefix}/batch-job-queue-summarizer"
  type  = "String"
  value = aws_batch_job_queue.summarizer.name
  tags  = var.tags
}

resource "aws_ssm_parameter" "batch_job_definition_summarizer" {
  name  = "${local.ssm_param_prefix}/batch-job-definition-summarizer"
  type  = "String"
  value = aws_batch_job_definition.summarizer.name
  tags  = var.tags
}

# CloudWatch Event resources for scheduling
resource "aws_iam_role" "event_rule" {
  name = local.resource_names.iam_role_event_rule

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

# Least-privilege policy for the EventBridge rule role: submit the project's Batch
# jobs and invoke the project's Lambda functions. Replaces AWSBatchFullAccess +
# AWSLambdaRole managed policies.
data "aws_iam_policy_document" "event_rule" {
  statement {
    sid       = "BatchSubmit"
    effect    = "Allow"
    actions   = ["batch:SubmitJob"]
    resources = local.project_resource_arns.batch_resources
  }

  statement {
    sid       = "LambdaInvoke"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:${local.partition}:lambda:${local.region}:${local.account_id}:function:${local.name_prefix}-*"]
  }
}

resource "aws_iam_role_policy" "event_rule_scoped" {
  name   = "${local.name_prefix}-event-scoped"
  role   = aws_iam_role.event_rule.name
  policy = data.aws_iam_policy_document.event_rule.json
}

resource "aws_cloudwatch_event_rule" "indexer" {
  count               = var.use_graph_rag ? 1 : 0
  name                = local.resource_names.cloudwatch_indexer
  description         = "Schedule for running the indexer batch job"
  schedule_expression = var.indexer_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_rule" "cleaner" {
  count               = var.use_graph_rag ? 1 : 0
  name                = local.resource_names.cloudwatch_cleaner
  description         = "Schedule for running the cleaner lambda function"
  schedule_expression = var.cleaner_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_rule" "summarizer" {
  name                = "${local.resource_names.cloudwatch_summarizer}-ko"
  description         = "Schedule for running the summarizer batch job"
  schedule_expression = var.summarizer_schedule_expression
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "indexer" {
  count    = var.use_graph_rag ? 1 : 0
  rule     = aws_cloudwatch_event_rule.indexer[0].name
  arn      = aws_batch_job_queue.indexer[0].arn
  role_arn = aws_iam_role.event_rule.arn

  batch_target {
    job_definition = aws_batch_job_definition.indexer[0].arn
    job_name       = "${local.name_prefix}-indexing"
    job_attempts   = local.default_configs.indexer_batch.retry
  }

  input = jsonencode({
    Parameters = {
      target_date   = "null",
      days_to_fetch = "0",
      arxiv_ids     = "null"
    }
  })
}

resource "aws_cloudwatch_event_target" "cleaner" {
  count = var.use_graph_rag ? 1 : 0
  rule  = aws_cloudwatch_event_rule.cleaner[0].name
  arn   = aws_lambda_function.cleaner[0].arn
}

resource "aws_cloudwatch_event_target" "summarizer" {
  rule     = aws_cloudwatch_event_rule.summarizer.name
  arn      = aws_batch_job_queue.summarizer.arn
  role_arn = aws_iam_role.event_rule.arn

  batch_target {
    job_definition = aws_batch_job_definition.summarizer.arn
    job_name       = "${local.name_prefix}-summarizing-ko"
    job_attempts   = local.default_configs.summarizer_batch.retry
  }

  input = jsonencode({
    Parameters = {
      target_date         = "null",
      days_to_fetch       = "0",
      arxiv_ids           = "null",
      language            = "ko",
      apply_retrieval     = "true",
      send_business_slack = "true"
    }
  })
}

resource "aws_lambda_permission" "cloudwatch_to_cleaner" {
  count         = var.use_graph_rag ? 1 : 0
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cleaner[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleaner[0].arn
}

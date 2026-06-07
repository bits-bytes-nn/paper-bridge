variable "project_name" {
  description = "Name of the project"
  type        = string
  nullable    = false
}

variable "stage" {
  description = "Deployment stage; must match the app config (Resources.stage). Used to namespace SSM parameters and their IAM scope as /{project_name}-{stage}/*."
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.stage)
    error_message = "stage must be one of: dev, prod"
  }
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

variable "bedrock_region_name" {
  description = "Bedrock region name for resources"
  type        = string
  default     = "us-west-2"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+-[0-9]+$", var.bedrock_region_name))
    error_message = "Region name must be in a valid AWS region format (e.g., us-west-2, eu-central-1)"
  }
}

variable "use_graph_rag" {
  description = "Whether to apply the graph RAG"
  type        = bool
  default     = false
}

variable "vpc_id" {
  description = "ID of the VPC where resources will be deployed"
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-f0-9]{8,}$", var.vpc_id))
    error_message = "VPC ID must be a valid vpc-* identifier"
  }
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs where resources will be deployed"
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) > 0
    error_message = "At least one private subnet ID must be provided"
  }

  validation {
    condition     = alltrue([for id in var.private_subnet_ids : can(regex("^subnet-[a-f0-9]{8,}$", id))])
    error_message = "All subnet IDs must be valid subnet-* identifiers"
  }
}

variable "root_dir" {
  description = "Root directory of the project"
  type        = string
  nullable    = false
}

variable "codebuild_source_bucket" {
  description = "S3 bucket for storing CodeBuild source code"
  type        = string
  nullable    = false
}

variable "email_address" {
  description = "Email address for notifications and alerts"
  type        = string
  default     = null

  validation {
    condition     = var.email_address == null || can(regex("^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.email_address))
    error_message = "Email address must be in a valid format"
  }
}

variable "indexer_schedule_expression" {
  description = "Schedule expression for the indexing job (AWS cron or rate expression)"
  type        = string
  default     = "cron(0 0 * * ? *)"

  validation {
    condition     = can(regex("^(cron|rate)\\(.*\\)$", var.indexer_schedule_expression))
    error_message = "Schedule expression must be a valid AWS cron or rate expression"
  }
}

variable "cleaner_schedule_expression" {
  description = "Schedule expression for the cleaner job (AWS cron or rate expression)"
  type        = string
  default     = "cron(0 0 * * ? *)"

  validation {
    condition     = can(regex("^(cron|rate)\\(.*\\)$", var.cleaner_schedule_expression))
    error_message = "Schedule expression must be a valid AWS cron or rate expression"
  }
}

variable "summarizer_schedule_expression" {
  description = "Schedule expression for the summarizer job (AWS cron or rate expression)"
  type        = string
  default     = "cron(0 0 * * ? *)"

  validation {
    condition     = can(regex("^(cron|rate)\\(.*\\)$", var.summarizer_schedule_expression))
    error_message = "Schedule expression must be a valid AWS cron or rate expression"
  }
}

variable "business_slack_bot_token" {
  description = "Slack bot token"
  type        = string
  default     = null

  validation {
    condition     = var.business_slack_bot_token == null || can(regex("^xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]+$", var.business_slack_bot_token))
    error_message = "Slack bot token must be in the format 'xoxb-' followed by numbers, dash, numbers, dash, and alphanumeric characters"
  }
}

variable "business_slack_channel_id" {
  description = "Slack channel ID"
  type        = string
  default     = null

  validation {
    condition     = var.business_slack_channel_id == null || can(regex("^C[A-Z0-9]{8,}$", var.business_slack_channel_id))
    error_message = "Slack channel must be in the format 'C' followed by alphanumeric characters"
  }
}
variable "llama_cloud_api_key" {
  description = "API key for the LLAMA Cloud API"
  type        = string
  default     = null

  validation {
    condition     = var.llama_cloud_api_key == null || can(regex("^llx-[A-Za-z0-9]{48}$", var.llama_cloud_api_key))
    error_message = "LLAMA Cloud API key must be in the format 'llx-' followed by 48 alphanumeric characters"
  }
}

variable "personal_slack_bot_token" {
  description = "Slack bot token"
  type        = string
  default     = null

  validation {
    condition     = var.personal_slack_bot_token == null || can(regex("^xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]+$", var.personal_slack_bot_token))
    error_message = "Slack bot token must be in the format 'xoxb-' followed by numbers, dash, numbers, dash, and alphanumeric characters"
  }
}

variable "personal_slack_channel_id" {
  description = "Slack channel ID"
  type        = string
  default     = null

  validation {
    condition     = var.personal_slack_channel_id == null || can(regex("^C[A-Z0-9]{8,}$", var.personal_slack_channel_id))
    error_message = "Slack channel must be in the format 'C' followed by alphanumeric characters"
  }
}

variable "upstage_api_key" {
  description = "Upstage API key"
  type        = string
  default     = null

  validation {
    condition     = var.upstage_api_key == null || can(regex("^up_[A-Za-z0-9]{29}$", var.upstage_api_key))
    error_message = "Upstage API key must be in the format 'up_' followed by 29 alphanumeric characters"
  }
}

variable "github_token" {
  description = "GitHub personal access token used by the summarizer to open paper-summary PRs. Only needed when output.mode = 'github'."
  type        = string
  default     = null
  sensitive   = true
}

variable "github_repo_name" {
  description = "Target GitHub repository in 'owner/name' form where the summarizer opens paper-summary PRs. Only needed when output.mode = 'github'."
  type        = string
  default     = null
}

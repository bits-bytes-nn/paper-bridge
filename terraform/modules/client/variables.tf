variable "project_name" {
  description = "Name of the project"
  type        = string
  nullable    = false
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
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

variable "llama_cloud_api_key" {
  description = "API key for the LLAMA Cloud API"
  type        = string
  default     = null

  validation {
    condition     = var.llama_cloud_api_key == null || can(regex("^llx-[A-Za-z0-9]{48}$", var.llama_cloud_api_key))
    error_message = "LLAMA Cloud API key must be in the format 'llx-' followed by 48 alphanumeric characters"
  }
}

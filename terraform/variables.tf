variable "project_name" {
  description = "Name of the project"
  type        = string
  nullable    = false
  default     = "paper-bridge"
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-west-2"
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "172.30.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "VPC CIDR must be a valid IPv4 CIDR block"
  }
}

variable "max_azs" {
  description = "Maximum number of Availability Zones to use"
  type        = number
  default     = 3

  validation {
    condition     = var.max_azs >= 2 && var.max_azs <= 3
    error_message = "max_azs must be between 2 and 3"
  }
}

variable "nat_gateways" {
  description = "Number of NAT Gateways to create"
  type        = number
  default     = 1

  validation {
    condition     = var.nat_gateways > 0
    error_message = "Number of NAT gateways must be greater than 0"
  }
}

variable "enable_vpn" {
  description = "Whether to enable Client VPN endpoint"
  type        = bool
  default     = false
}

variable "vpn_client_cidr_block" {
  description = "CIDR block to assign to VPN clients"
  type        = string
  default     = "10.100.0.0/22"

  validation {
    condition     = can(cidrhost(var.vpn_client_cidr_block, 0))
    error_message = "VPN client CIDR must be a valid IPv4 CIDR block"
  }

  validation {
    condition     = !var.enable_vpn || var.vpn_client_cidr_block != ""
    error_message = "VPN client CIDR block must be provided when VPN is enabled"
  }
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

variable "neptune_instance_type" {
  description = "Neptune database instance type"
  type        = string
  default     = "db.serverless"

  validation {
    condition     = contains(["db.serverless", "db.r6g.large", "db.r6g.xlarge", "db.r6g.2xlarge"], var.neptune_instance_type)
    error_message = "Invalid instance type. Must be one of: db.serverless, db.r6g.large, db.r6g.xlarge, db.r6g.2xlarge"
  }
}

variable "neptune_min_capacity" {
  description = "Minimum Neptune Capacity Units (NCU)"
  type        = number
  default     = 2.5

  validation {
    condition     = var.neptune_min_capacity >= 1.0 && var.neptune_min_capacity <= 128.0
    error_message = "Minimum NCU must be between 1.0 and 128.0"
  }
}

variable "neptune_max_capacity" {
  description = "Maximum Neptune Capacity Units (NCU)"
  type        = number
  default     = 32.0

  validation {
    condition     = var.neptune_max_capacity >= var.neptune_min_capacity && var.neptune_max_capacity <= 128.0
    error_message = "Maximum NCU must be between minimum NCU and 128.0"
  }
}

variable "neptune_engine_version" {
  description = "Neptune engine version"
  type        = string
  default     = "1.2.1.0"
}

variable "client_user_name" {
  description = "Username of the IAM user for database access"
  type        = string
  default     = null
}

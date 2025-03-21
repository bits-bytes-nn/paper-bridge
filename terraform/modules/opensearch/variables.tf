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

variable "client_security_group_ids" {
  description = "Security group IDs for client access"
  type        = list(string)

  validation {
    condition     = length(var.client_security_group_ids) > 0
    error_message = "At least one security group ID is required"
  }

  validation {
    condition     = alltrue([for id in var.client_security_group_ids : can(regex("^sg-[a-f0-9]{8,}$", id))])
    error_message = "All security group IDs must be valid sg-* identifiers"
  }
}

variable "client_role_arn" {
  description = "ARN of the client IAM role"
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/.+$", var.client_role_arn))
    error_message = "Client role ARN must be a valid IAM role ARN in the format arn:aws:iam::[account-id]:role/[role-name]"
  }
}

variable "enable_vpn" {
  description = "Whether to enable Client VPN endpoint for secure OpenSearch access"
  type        = bool
  default     = false
}

variable "client_user_name" {
  description = "Name of the IAM user for OpenSearch access when VPN is enabled"
  type        = string
  default     = null

  validation {
    condition     = !var.enable_vpn || var.client_user_name != null
    error_message = "client_user_name must be provided when enable_vpn is true"
  }
}

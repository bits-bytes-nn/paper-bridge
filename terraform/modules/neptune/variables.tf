variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-f0-9]{8,}$", var.vpc_id))
    error_message = "VPC ID must be a valid vpc-* identifier"
  }
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs"
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_ids) > 0
    error_message = "At least one private subnet ID is required"
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

variable "db_instance_type" {
  description = "Neptune instance type"
  type        = string
  default     = "db.serverless"

  validation {
    condition     = contains(["db.serverless", "db.r6g.large", "db.r6g.xlarge", "db.r6g.2xlarge"], var.db_instance_type)
    error_message = "Invalid instance type. Must be one of: db.serverless, db.r6g.large, db.r6g.xlarge, db.r6g.2xlarge"
  }
}

variable "min_ncu" {
  description = "Minimum Neptune Capacity Units (NCU)"
  type        = number
  default     = 2.5

  validation {
    condition     = var.min_ncu >= 1.0 && var.min_ncu <= 128.0
    error_message = "Minimum NCU must be between 1.0 and 128.0"
  }
}

variable "max_ncu" {
  description = "Maximum Neptune Capacity Units (NCU)"
  type        = number
  default     = 32

  validation {
    condition     = var.max_ncu >= var.min_ncu && var.max_ncu <= 128.0
    error_message = "Maximum NCU must be between minimum NCU and 128.0"
  }
}

variable "engine_version" {
  description = "Neptune engine version"
  type        = string
  default     = "1.4.1.0"

  validation {
    condition     = can(regex("^\\d+\\.\\d+\\.\\d+\\.\\d+$", var.engine_version))
    error_message = "Engine version must be in format X.X.X.X"
  }
}

variable "enable_audit_log" {
  description = "Enable audit logging"
  type        = bool
  default     = false
}


variable "enable_vpn" {
  description = "Whether to enable Client VPN endpoint"
  type        = bool
  default     = false
}
variable "vpn_security_group_ids" {
  description = "Security group IDs for VPN access"
  type        = list(string)
  default     = []
  validation {
    condition     = !var.enable_vpn || length(var.vpn_security_group_ids) > 0
    error_message = "At least one security group ID is required when VPN is enabled"
  }
  validation {
    condition     = !var.enable_vpn || alltrue([for id in var.vpn_security_group_ids : can(regex("^sg-[a-f0-9]{8,}$", id))])
    error_message = "All security group IDs must be valid sg-* identifiers"
  }
}

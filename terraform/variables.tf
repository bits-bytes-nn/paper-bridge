variable "project_name" {
  description = "Name of the project"
  type        = string
  default     = "paper-bridge"
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
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
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[1-2][0-9]|3[0-2])$", var.vpn_client_cidr_block))
    error_message = "VPN client CIDR must be a valid IPv4 CIDR block"
  }
}

variable "client_user_name" {
  description = "Name of the IAM user for DB access"
  type        = string
  default     = null
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

variable "enable_audit_log" {
  description = "Enable audit logging"
  type        = bool
  default     = false
}

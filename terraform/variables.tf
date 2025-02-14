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

variable "deploy_notebook" {
  description = "Whether to deploy a SageMaker notebook instance"
  type        = bool
  default     = false
}

variable "notebook_instance_type" {
  description = "Instance type for SageMaker notebook"
  type        = string
  default     = null

  validation {
    condition     = var.deploy_notebook ? contains(["ml.m5.xlarge", "ml.p3.2xlarge"], var.notebook_instance_type) : true
    error_message = "When deploy_notebook is true, instance type must be either ml.m5.xlarge or ml.p3.2xlarge"
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

variable "enable_audit_log" {
  description = "Enable audit logging"
  type        = bool
  default     = false
}

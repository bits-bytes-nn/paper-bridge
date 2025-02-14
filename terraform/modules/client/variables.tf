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
  description = "VPC ID where Neptune will be deployed"
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-f0-9]{8,}$", var.vpc_id))
    error_message = "VPC ID must be a valid vpc-* identifier"
  }
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs"
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_ids) > 0
    error_message = "At least one public subnet ID is required"
  }

  validation {
   condition     = alltrue([for id in var.public_subnet_ids : can(regex("^subnet-[a-f0-9]{8,}$", id))])
   error_message = "All subnet IDs must be valid subnet-* identifiers"
 }
}

variable "deploy_notebook" {
  description = "Whether to deploy a SageMaker notebook instance"
  type        = bool
  default     = false
}

variable "notebook_instance_type" {
  description = "Instance type for SageMaker notebook"
  type        = string
  default = null

  validation {
    condition     = var.deploy_notebook ? contains(["ml.m5.xlarge", "ml.p3.2xlarge"], var.notebook_instance_type) : true
    error_message = "When deploy_notebook is true, instance type must be either ml.m5.xlarge or ml.p3.2xlarge"
  }
}

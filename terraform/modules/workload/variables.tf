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

variable "deploy_bastion_host" {
  description = "Whether to deploy a bastion host"
  type        = bool
  default     = false
}

variable "allowed_ip_ranges" {
  description = "List of allowed IP CIDR ranges"
  type        = list(string)

  validation {
    condition     = length(var.allowed_ip_ranges) > 0 && alltrue([
      for cidr in var.allowed_ip_ranges : can(cidrhost(cidr, 0))
    ])
    error_message = "At least one valid CIDR range must be provided (e.g. 10.0.0.0/16)"
  }
}

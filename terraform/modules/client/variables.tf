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

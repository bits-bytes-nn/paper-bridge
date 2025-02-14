variable "project_name" {
  description = "Name of the project"
  type        = string
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
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[1-2][0-9]|3[0-2])$", var.vpc_cidr))
    error_message = "VPC CIDR must be a valid IPv4 CIDR block"
  }
}

variable "max_azs" {
  description = "Maximum number of AZs to use"
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
    condition     = var.nat_gateways > 0 && var.nat_gateways <= var.max_azs
    error_message = "Number of NAT gateways must be between 1 and max_azs"
  }
}

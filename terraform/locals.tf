locals {
  common_tags = {
    Environment = terraform.workspace
    Project     = var.project_name
    ManagedBy   = "terraform"
    CostCenter  = var.project_name
  }
}

locals {
  root_dir = "${path.module}/.."

  common_tags = merge({
    Project     = var.project_name
    Environment = terraform.workspace
    ManagedBy   = "terraform"
    CostCenter  = var.project_name
  }, var.tags)
}

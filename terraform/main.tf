provider "aws" {
  region = var.aws_region
}

module "base" {
  source = "./modules/base"

  project_name = var.project_name
  tags         = local.common_tags
}

module "workload" {
  source = "./modules/workload"

  project_name        = var.project_name
  tags               = local.common_tags
  vpc_id             = module.base.vpc_id
  public_subnet_ids  = module.base.public_subnet_ids
  deploy_bastion_host = var.deploy_bastion_host
  allowed_ip_ranges  = var.allowed_ip_ranges

  depends_on = [module.base]
}

module "neptune" {
  source = "./modules/neptune"

  project_name                    = var.project_name
  vpc_id                         = module.base.vpc_id
  private_subnet_ids             = module.base.private_subnet_ids
  bastion_host_security_group_ids = [module.workload.bastion_host_security_group_id]
  app_client_security_group_ids   = [module.workload.app_client_security_group_id]
  db_instance_type               = var.db_instance_type
  min_ncu                        = var.min_ncu
  max_ncu                        = var.max_ncu
  enable_audit_log               = var.enable_audit_log
  tags                           = local.common_tags

  depends_on = [module.base, module.workload]
}

module "opensearch" {
  source = "./modules/opensearch"

  project_name                    = var.project_name
  vpc_id                         = module.base.vpc_id
  private_subnet_ids             = module.base.private_subnet_ids
  deploy_bastion_host            = var.deploy_bastion_host
  bastion_user_name              = var.bastion_user_name
  bastion_host_security_group_ids = [module.workload.bastion_host_security_group_id]
  app_client_security_group_ids   = [module.workload.app_client_security_group_id]
  workload_role_arn              = module.workload.workload_role_arn
  tags                           = local.common_tags

  depends_on = [module.base, module.workload]
}

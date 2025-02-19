provider "aws" {
  region = var.aws_region
}

module "base" {
  source = "./modules/base"

  project_name = var.project_name
  enable_vpn   = var.enable_vpn
  vpn_client_cidr_block = var.vpn_client_cidr_block
  tags         = local.common_tags
}

module "client" {
  source = "./modules/client"

  project_name        = var.project_name
  vpc_id             = module.base.vpc_id
  tags               = local.common_tags

  depends_on = [module.base]
}

module "neptune" {
  source = "./modules/neptune"

  project_name                    = var.project_name
  vpc_id                         = module.base.vpc_id
  private_subnet_ids             = module.base.private_subnet_ids
  client_security_group_ids   = [module.client.client_security_group_id]
  db_instance_type               = var.db_instance_type
  min_ncu                        = var.min_ncu
  max_ncu                        = var.max_ncu
  enable_audit_log               = var.enable_audit_log
  enable_vpn                     = var.enable_vpn
  vpn_security_group_ids         = [module.base.vpn_security_group_id]
  tags                           = local.common_tags

  depends_on = [module.base, module.client]
}

module "opensearch" {
  source = "./modules/opensearch"

  project_name                    = var.project_name
  vpc_id                         = module.base.vpc_id
  private_subnet_ids             = module.base.private_subnet_ids
  client_security_group_ids   = [module.client.client_security_group_id]
  client_role_arn              = module.client.client_role_arn
  enable_vpn                     = var.enable_vpn
  vpn_security_group_ids         = [module.base.vpn_security_group_id]
  client_user_name               = var.client_user_name
  tags                           = local.common_tags

  depends_on = [module.base, module.client]
}

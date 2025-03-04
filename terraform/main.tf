provider "aws" {
  region = var.aws_region
}

module "base" {
  source = "./modules/base"

  project_name          = var.project_name
  vpc_cidr              = var.vpc_cidr
  max_azs               = var.max_azs
  nat_gateways          = var.nat_gateways
  enable_vpn            = var.enable_vpn
  root_dir              = local.root_dir
  vpn_client_cidr_block = var.vpn_client_cidr_block
  tags                  = local.common_tags
}

module "client" {
  source = "./modules/client"

  project_name                = var.project_name
  vpc_id                      = module.base.vpc.id
  private_subnet_ids          = module.base.private_subnet_ids
  root_dir                    = local.root_dir
  email_address               = var.email_address
  indexer_schedule_expression = var.indexer_schedule_expression
  cleaner_schedule_expression = var.cleaner_schedule_expression
  llama_cloud_api_key         = var.llama_cloud_api_key
  tags                        = local.common_tags

  depends_on = [module.base]
}

module "neptune" {
  source = "./modules/neptune"

  project_name              = var.project_name
  vpc_id                    = module.base.vpc.id
  private_subnet_ids        = module.base.private_subnet_ids
  client_security_group_ids = [module.client.security_group_id]
  vpn_security_group_ids    = var.enable_vpn ? [module.base.vpn_security_group_id] : []
  db_instance_type          = var.neptune_instance_type
  min_ncu                   = var.neptune_min_capacity
  max_ncu                   = var.neptune_max_capacity
  engine_version            = var.neptune_engine_version
  tags                      = local.common_tags

  depends_on = [module.base, module.client]
}

module "opensearch" {
  source = "./modules/opensearch"

  project_name              = var.project_name
  vpc_id                    = module.base.vpc.id
  private_subnet_ids        = module.base.private_subnet_ids
  client_security_group_ids = [module.client.security_group_id]
  client_role_arn           = module.client.iam_role.arn
  enable_vpn                = var.enable_vpn
  client_user_name          = var.enable_vpn ? var.client_user_name : null
  tags                      = local.common_tags

  depends_on = [module.base, module.client]
}

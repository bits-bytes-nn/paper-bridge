provider "aws" {
  region = var.default_region_name
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

  project_name                   = var.project_name
  stage                          = var.stage
  bedrock_region_name            = var.bedrock_region_name
  use_graph_rag                  = var.use_graph_rag
  vpc_id                         = module.base.vpc.id
  private_subnet_ids             = module.base.private_subnet_ids
  root_dir                       = local.root_dir
  codebuild_source_bucket        = var.codebuild_source_bucket
  email_address                  = var.email_address
  indexer_schedule_expression    = var.indexer_schedule_expression
  cleaner_schedule_expression    = var.cleaner_schedule_expression
  summarizer_schedule_expression = var.summarizer_schedule_expression
  business_slack_bot_token       = var.business_slack_bot_token
  business_slack_channel_id      = var.business_slack_channel_id
  llama_cloud_api_key            = var.llama_cloud_api_key
  personal_slack_bot_token       = var.personal_slack_bot_token
  personal_slack_channel_id      = var.personal_slack_channel_id
  upstage_api_key                = var.upstage_api_key
  tags                           = local.common_tags

  depends_on = [module.base]
}
module "neptune" {
  count  = var.use_graph_rag ? 1 : 0
  source = "./modules/neptune"

  project_name              = var.project_name
  stage                     = var.stage
  vpc_id                    = module.base.vpc.id
  private_subnet_ids        = module.base.private_subnet_ids
  client_security_group_ids = [module.client.security_group_id]
  vpn_security_group_ids    = var.enable_vpn ? module.base.vpn_security_group_ids : []
  db_instance_type          = var.neptune_instance_type
  min_ncu                   = var.neptune_min_capacity
  max_ncu                   = var.neptune_max_capacity
  engine_version            = var.neptune_engine_version
  skip_final_snapshot       = var.neptune_skip_final_snapshot
  enable_audit_log          = var.neptune_enable_audit_log
  # Single-instance research/dev cluster with no production traffic: apply engine
  # and parameter changes immediately rather than deferring to a maintenance
  # window (so a major-version upgrade actually lands during apply).
  apply_immediately = true
  tags              = local.common_tags

  depends_on = [module.base, module.client]
}

module "opensearch" {
  count  = var.use_graph_rag ? 1 : 0
  source = "./modules/opensearch"

  project_name              = var.project_name
  stage                     = var.stage
  vpc_id                    = module.base.vpc.id
  private_subnet_ids        = module.base.private_subnet_ids
  client_security_group_ids = [module.client.security_group_id]
  vpn_security_group_ids    = var.enable_vpn ? module.base.vpn_security_group_ids : []
  client_role_arn           = module.client.iam_role.arn
  client_role_name          = module.client.iam_role.name
  enable_vpn                = var.enable_vpn
  client_user_name          = var.enable_vpn ? var.client_user_name : null
  allow_public_access       = var.opensearch_allow_public_access
  tags                      = local.common_tags

  depends_on = [module.base, module.client]
}

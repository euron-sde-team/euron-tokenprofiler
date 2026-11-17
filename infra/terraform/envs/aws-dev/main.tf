provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}

locals {
  name = "${var.project}-${var.environment}"
  tags = { Project = var.project, Environment = var.environment, ManagedBy = "terraform" }
}

data "aws_caller_identity" "current" {}

module "network" {
  source             = "../../modules/network"
  name               = local.name
  single_nat_gateway = true
}

module "ecr" {
  source       = "../../modules/ecr"
  name         = local.name
  force_delete = true
}

module "storage" {
  source        = "../../modules/storage"
  name          = "${local.name}-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

module "secrets" {
  source                  = "../../modules/secrets"
  name                    = local.name
  recovery_window_in_days = 0
}

module "notifications" {
  source      = "../../modules/notifications"
  name        = local.name
  alert_email = var.alert_email
}

module "alb" {
  source            = "../../modules/alb"
  name              = local.name
  vpc_id            = module.network.vpc_id
  vpc_cidr          = "10.60.0.0/16"
  public_subnet_ids = module.network.public_subnet_ids
  certificate_arn   = var.certificate_arn
  ingress_cidr      = var.ingress_cidr
}

module "host" {
  source                = "../../modules/app-host"
  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  target_group_arns     = [module.alb.api_target_group_arn, module.alb.console_target_group_arn]
  instance_type         = var.instance_type
  volume_gb             = var.volume_gb
  secret_arn            = module.secrets.secret_arn
  reports_bucket        = module.storage.bucket
  reports_bucket_arn    = module.storage.bucket_arn
  ecr_repository_arns   = values(module.ecr.repository_arns)
  api_image             = "${module.ecr.repository_urls["api"]}:${var.image_tag}"
  console_image         = "${module.ecr.repository_urls["console"]}:${var.image_tag}"
}

module "observability" {
  source                      = "../../modules/observability"
  name                        = local.name
  alarm_topic_arn             = module.notifications.topic_arn
  alb_arn_suffix              = module.alb.arn_suffix
  api_target_group_arn_suffix = module.alb.api_target_group_arn_suffix
  autoscaling_group_name      = module.host.autoscaling_group_name
}

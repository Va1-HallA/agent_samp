provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project
      Env     = var.env
      Managed = "terraform"
    }
  }
}

locals {
  name_prefix = "${var.project}-${var.env}"
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }

# ---------- IAM task role (root-level to break compute ↔ search cycle) ----------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name_prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# ---------- Modules ----------

module "networking" {
  source = "./modules/networking"

  name_prefix          = local.name_prefix
  vpc_cidr             = var.vpc_cidr
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  availability_zones   = data.aws_availability_zones.available.names
  allowed_admin_cidr   = var.allowed_admin_cidr
}

module "database" {
  source = "./modules/database"

  name_prefix          = local.name_prefix
  private_subnet_ids   = module.networking.private_subnet_ids
  rds_sg_id            = module.networking.rds_sg_id
  instance_class       = var.rds_instance_class
  allocated_storage_gb = var.rds_allocated_storage_gb
  db_name              = var.db_name
  db_username          = var.db_username
}

module "sessions" {
  source = "./modules/sessions"

  name_prefix = local.name_prefix
}

module "search" {
  source = "./modules/search"

  name_prefix        = local.name_prefix
  region             = var.region
  account_id         = data.aws_caller_identity.current.account_id
  private_subnet_ids = module.networking.private_subnet_ids
  opensearch_sg_id   = module.networking.opensearch_sg_id
  task_role_arn      = aws_iam_role.ecs_task.arn
  instance_type      = var.opensearch_instance_type
  instance_count     = var.opensearch_instance_count
  volume_gb          = var.opensearch_volume_gb
}

module "compute" {
  source = "./modules/compute"

  name_prefix        = local.name_prefix
  region             = var.region
  env                = var.env
  vpc_id             = module.networking.vpc_id
  public_subnet_ids  = module.networking.public_subnet_ids
  private_subnet_ids = module.networking.private_subnet_ids
  alb_sg_id          = module.networking.alb_sg_id
  ecs_tasks_sg_id    = module.networking.ecs_tasks_sg_id

  task_role_arn         = aws_iam_role.ecs_task.arn
  task_role_name        = aws_iam_role.ecs_task.name
  rds_secret_arn        = module.database.secret_arn
  rds_secret_name       = module.database.secret_name
  opensearch_endpoint   = module.search.endpoint
  opensearch_domain_arn = module.search.domain_arn
  dynamodb_table_name   = module.sessions.table_name
  dynamodb_table_arn    = module.sessions.table_arn

  bedrock_model_id           = var.bedrock_model_id
  bedrock_router_model_id    = var.bedrock_router_model_id
  bedrock_embedding_model_id = var.bedrock_embedding_model_id

  container_image = var.container_image
  task_cpu        = var.task_cpu
  task_memory     = var.task_memory
  desired_count   = var.desired_count
}

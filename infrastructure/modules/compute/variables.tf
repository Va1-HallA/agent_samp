variable "name_prefix" {
  type = string
}

variable "region" {
  type = string
}

variable "env" {
  type = string
}

# --- Networking ---

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "alb_sg_id" {
  type = string
}

variable "ecs_tasks_sg_id" {
  type = string
}

# --- IAM (task role created at root level) ---

variable "task_role_arn" {
  type = string
}

variable "task_role_name" {
  type = string
}

# --- Service dependencies ---

variable "rds_secret_arn" {
  type = string
}

variable "rds_secret_name" {
  type = string
}

variable "opensearch_endpoint" {
  type = string
}

variable "opensearch_domain_arn" {
  type = string
}

variable "dynamodb_table_name" {
  type = string
}

variable "dynamodb_table_arn" {
  type = string
}

# --- Bedrock ---

variable "bedrock_model_id" {
  type = string
}

variable "bedrock_router_model_id" {
  type = string
}

variable "bedrock_embedding_model_id" {
  type = string
}

# --- ECS ---

variable "container_image" {
  type    = string
  default = ""
}

variable "task_cpu" {
  type    = string
  default = "1024"
}

variable "task_memory" {
  type    = string
  default = "2048"
}

variable "desired_count" {
  type    = number
  default = 1
}

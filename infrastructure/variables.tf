variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix applied to every resource."
  type        = string
  default     = "care-agent"
}

variable "env" {
  description = "Deployment environment (dev / stage / prod)."
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.0.0/24", "10.20.1.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.10.0/24", "10.20.11.0/24"]
}

# ---------- RDS ----------

variable "rds_instance_class" {
  description = "Cheap but still PostgreSQL-capable. Bump to r6g for prod."
  type        = string
  default     = "db.t4g.small"
}

variable "rds_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_name" {
  type    = string
  default = "careagent"
}

variable "db_username" {
  type    = string
  default = "careagent"
}

# ---------- OpenSearch ----------

variable "opensearch_instance_type" {
  type    = string
  default = "t3.small.search"
}

variable "opensearch_instance_count" {
  type    = number
  default = 1
}

variable "opensearch_volume_gb" {
  type    = number
  default = 10
}

# ---------- Bedrock model IDs ----------

variable "bedrock_model_id" {
  type    = string
  default = "anthropic.claude-sonnet-4-20250514-v1:0"
}

variable "bedrock_router_model_id" {
  type    = string
  default = "anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

# ---------- ECS ----------

variable "container_image" {
  description = "Full ECR image URI (e.g. <acct>.dkr.ecr.us-east-1.amazonaws.com/care-agent:latest)."
  type        = string
  default     = ""
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

# ---------- Admin access ----------

variable "allowed_admin_cidr" {
  description = "CIDR allowed to call ALB directly (for smoke-testing /health)."
  type        = string
  default     = "0.0.0.0/0"
}

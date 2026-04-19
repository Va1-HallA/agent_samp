variable "name_prefix" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "opensearch_sg_id" {
  type = string
}

variable "task_role_arn" {
  description = "ECS task role ARN for OpenSearch access policy."
  type        = string
}

variable "instance_type" {
  type    = string
  default = "t3.small.search"
}

variable "instance_count" {
  type    = number
  default = 1
}

variable "volume_gb" {
  type    = number
  default = 10
}

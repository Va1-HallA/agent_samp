output "alb_dns_name" {
  description = "Point your browser / curl here."
  value       = module.compute.alb_dns_name
}

output "ecr_repository_url" {
  description = "Push images with: docker tag ... <this>:latest && docker push"
  value       = module.compute.ecr_repository_url
}

output "opensearch_endpoint" {
  description = "Host-only form; the Python code prepends https://"
  value       = module.search.endpoint
}

output "rds_endpoint" {
  value     = module.database.rds_endpoint
  sensitive = true
}

output "dynamodb_sessions_table" {
  value = module.sessions.table_name
}

output "rds_secret_name" {
  value = module.database.secret_name
}

output "ecs_cluster_name" { value = module.compute.ecs_cluster_name }
output "ecs_service_name" { value = module.compute.ecs_service_name }

output "task_role_arn" { value = aws_iam_role.ecs_task.arn }

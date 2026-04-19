output "rds_endpoint" {
  value     = aws_db_instance.postgres.endpoint
  sensitive = true
}

output "rds_address" {
  value     = aws_db_instance.postgres.address
  sensitive = true
}

output "secret_arn" {
  value = aws_secretsmanager_secret.rds.arn
}

output "secret_name" {
  value = aws_secretsmanager_secret.rds.name
}

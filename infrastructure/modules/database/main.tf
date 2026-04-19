resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-rds-subnets"
  subnet_ids = var.private_subnet_ids
  tags       = { Name = "${var.name_prefix}-rds-subnets" }
}

resource "aws_db_instance" "postgres" {
  identifier                 = "${var.name_prefix}-rds"
  engine                     = "postgres"
  engine_version             = "15.5"
  instance_class             = var.instance_class
  allocated_storage          = var.allocated_storage_gb
  storage_encrypted          = true
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = random_password.db.result
  port                       = 5432
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [var.rds_sg_id]
  publicly_accessible        = false
  skip_final_snapshot        = true
  deletion_protection        = false
  auto_minor_version_upgrade = true
  backup_retention_period    = 3
  apply_immediately          = true

  tags = { Name = "${var.name_prefix}-rds" }
}

# ---------- Secrets Manager ----------

resource "aws_secretsmanager_secret" "rds" {
  name                    = "${var.name_prefix}/rds/credentials"
  description             = "RDS credentials consumed by ECS tasks"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "rds" {
  secret_id = aws_secretsmanager_secret.rds.id
  secret_string = jsonencode({
    engine   = "postgres"
    host     = aws_db_instance.postgres.address
    port     = aws_db_instance.postgres.port
    username = var.db_username
    password = random_password.db.result
    dbname   = var.db_name
  })
}

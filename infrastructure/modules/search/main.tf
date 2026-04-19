resource "aws_opensearch_domain" "main" {
  domain_name    = "${var.name_prefix}-kb"
  engine_version = "OpenSearch_2.11"

  cluster_config {
    instance_type  = var.instance_type
    instance_count = var.instance_count
  }

  ebs_options {
    ebs_enabled = true
    volume_size = var.volume_gb
    volume_type = "gp3"
  }

  vpc_options {
    subnet_ids         = [var.private_subnet_ids[0]]
    security_group_ids = [var.opensearch_sg_id]
  }

  node_to_node_encryption { enabled = true }
  encrypt_at_rest { enabled = true }
  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = var.task_role_arn }
      Action    = "es:*"
      Resource  = "arn:aws:es:${var.region}:${var.account_id}:domain/${var.name_prefix}-kb/*"
    }]
  })

  advanced_security_options {
    enabled                        = false
    anonymous_auth_enabled         = false
    internal_user_database_enabled = false
  }

  tags = { Name = "${var.name_prefix}-kb" }
}

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Swap to S3+DynamoDB remote state for team use.
  # backend "s3" {
  #   bucket         = "care-agent-tfstate"
  #   key            = "prod/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "care-agent-tfstate-lock"
  #   encrypt        = true
  # }
}

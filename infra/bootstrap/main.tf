# Terraform state backend — run once, before the main stack.
#
# This creates only the S3 bucket that holds the main stack's state. It uses
# LOCAL state itself, because a backend cannot store the state of its own
# creation. That local state file is unimportant: if it is lost, the bucket can
# be re-adopted with `terraform import`.
#
# The bucket INTENTIONALLY SURVIVES `terraform destroy` of the main stack. It is
# a separate root module for exactly that reason — destroying the demo must not
# be able to take the state with it.
#
#   cd infra/bootstrap
#   terraform init
#   terraform apply
#
# No DynamoDB lock table. Terraform 1.10+ locks S3 state natively with
# `use_lockfile = true`, which the main stack's backend block sets.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "reconai"
      ManagedBy = "terraform"
      Component = "tf-state"
    }
  }
}

variable "aws_region" {
  description = "Region for the state bucket. Keep it the same as the main stack."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = <<-EOT
    Globally unique S3 bucket name for Terraform state.

    S3 bucket names are global, so this must be unique across all of AWS.
    Suffix it with something account-specific, e.g. "reconai-tfstate-pn1109".
  EOT
  type        = string
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # No force_destroy. Deleting state by accident is exactly the failure this
  # bucket exists to prevent; emptying it must be a deliberate manual act.
  lifecycle {
    prevent_destroy = true
  }
}

# Versioning is what makes a corrupted or truncated state recoverable.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# State carries resource attributes. Nothing about it should ever be reachable
# from the internet.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  description = "Put this in the main stack's backend block as `bucket`."
  value       = aws_s3_bucket.state.id
}

output "backend_block" {
  description = "Copy this into infra/backend.tf, then run `terraform init` in infra/."
  value       = <<-EOT
    terraform {
      backend "s3" {
        bucket       = "${aws_s3_bucket.state.id}"
        key          = "reconai/demo/terraform.tfstate"
        region       = "${var.aws_region}"
        encrypt      = true
        use_lockfile = true
      }
    }
  EOT
}

# ReconAI — demo deployment on AWS.
#
# One root module, files split by concern. Deliberately not wrapped in reusable
# modules: modules earn their keep when reused across environments, and there is
# exactly one environment here. Eleven single-use modules would add variable
# plumbing without removing duplication.
#
# Lifecycle this stack is built for:
#   apply (stage 1) -> push images -> create secrets -> apply (stage 2)
#   -> verify -> record -> destroy
#
# See README.md. Nothing here should be applied without reading it first.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Backend is intentionally NOT declared here. Run infra/bootstrap first, then
  # write its `backend_block` output to infra/backend.tf and `terraform init`.
  # Committing a backend block that points at a bucket nobody has created yet
  # makes `terraform init` fail before it can do anything useful.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

# CloudFront requires its ACM certificate in us-east-1 regardless of where the
# rest of the stack lives. This aliased provider exists solely for that, so the
# main region stays a free choice.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.project_name}-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  # Two AZs: the minimum an ALB requires.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  # SSM parameter ARNs are CONSTRUCTED, never read. A `data "aws_ssm_parameter"`
  # would pull the decrypted value into Terraform state, which is precisely what
  # the secret-ownership boundary forbids. The ECS agent resolves these at task
  # start; Terraform only ever handles the ARN string.
  ssm_prefix             = "arn:aws:ssm:${local.region}:${local.account_id}:parameter"
  anthropic_key_arn      = "${local.ssm_prefix}${var.anthropic_key_parameter_name}"
  agent_database_url_arn = "${local.ssm_prefix}${var.agent_database_url_parameter_name}"

  # Private DNS namespace for service-to-service traffic.
  service_domain = "${var.project_name}.local"

  # Gate for the second apply stage. The three application services cannot start
  # until images are in ECR and the human-owned secrets exist, so stage 1 leaves
  # them at zero.
  service_count = var.deploy_app_services ? 1 : 0
}

# CloudWatch log groups.
#
# Terraform owns them so `terraform destroy` removes them. Log groups created
# implicitly by the awslogs driver are NOT Terraform-owned and survive destroy,
# quietly retaining data forever — which is why each one is declared here.
#
# Retention is bounded for the same reason: unbounded retention is the slowest
# and least visible cost leak in an AWS account.

resource "aws_cloudwatch_log_group" "financial_core" {
  name              = "/ecs/${local.name}/financial-core"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "investigation_service" {
  name              = "/ecs/${local.name}/investigation-service"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "kafka" {
  name              = "/ecs/${local.name}/kafka"
  retention_in_days = var.log_retention_days
}

# --- Billing alarm: deliberately NOT created here --------------------------
#
# An account-level billing alarm needs the AWS/Billing EstimatedCharges metric,
# which only exists in us-east-1 AND only after "Receive Billing Alerts" has
# been enabled manually in the Billing console. It is an account preference, not
# an API-creatable resource.
#
# Declaring the alarm without that preference produces an alarm stuck in
# INSUFFICIENT_DATA — infrastructure that looks like a safety net and is not.
# The manual step is documented in README.md instead, which is honest about
# what protects the account.

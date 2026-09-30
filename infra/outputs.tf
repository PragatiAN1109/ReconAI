# Outputs are the interface between Terraform and the manual steps: DNS records
# at Namecheap, image pushes, and the two SSM parameters Terraform never owns.

output "stage" {
  description = "Which deployment stage this state reflects."
  value       = var.deploy_app_services ? "2 - application services running" : "1 - foundation only (no ECS tasks, no CloudFront)"
}

# --- Stage 1: DNS validation ----------------------------------------------

output "acm_validation_record" {
  description = <<-EOT
    Add this CNAME at Namecheap FIRST. ACM cannot issue the certificate until it
    is visible, and CloudFront cannot be created until the certificate is issued.

    Namecheap strips the zone suffix: enter only the host portion of `name`
    (everything before ".pragatinarote.com").
  EOT

  value = {
    for dvo in aws_acm_certificate.main.domain_validation_options :
    dvo.domain_name => {
      name  = dvo.resource_record_name
      type  = dvo.resource_record_type
      value = dvo.resource_record_value
    }
  }
}

# --- Stage 1: image push ---------------------------------------------------

output "ecr_repositories" {
  description = "Push images here before stage 2. See README for the login and push commands."
  value       = { for k, v in aws_ecr_repository.app : k => v.repository_url }
}

# --- Stage 1: database, for composing the Investigation Service's URL ------

output "db_endpoint" {
  description = "RDS hostname. Needed to compose the agent database URL."
  value       = aws_db_instance.main.address
}

output "db_master_secret_arn" {
  description = <<-EOT
    ARN of the RDS-managed master password secret.

    Terraform never reads the value — only this identifier. Retrieve the
    password with the command in README step 6 to compose the agent URL.
  EOT
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "agent_database_url_template" {
  description = <<-EOT
    Shape of the URL to store at the agent_database_url SSM parameter.

    <DB_PASSWORD> is a placeholder. Substitute the real password retrieved from
    the managed secret; never commit the result.
  EOT
  value       = "postgresql+asyncpg://${aws_db_instance.main.username}:<DB_PASSWORD>@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"
}

output "ssm_parameter_names" {
  description = "The two SecureStrings the human owns. Terraform creates neither."
  value = {
    anthropic_api_key  = var.anthropic_key_parameter_name
    agent_database_url = var.agent_database_url_parameter_name
  }
}

# --- Stage 2: the public entry point --------------------------------------

output "cloudfront_domain" {
  description = "CNAME target for the `reconai` record at Namecheap. Empty until stage 2."
  value       = try(aws_cloudfront_distribution.main[0].domain_name, "")
}

output "dns_setup" {
  description = "The two records to create at Namecheap, in order."
  value = var.deploy_app_services ? join("\n", [
    "1. ACM validation CNAME  -> see the acm_validation_record output (stage 1)",
    "2. CNAME  reconai  ->  ${try(aws_cloudfront_distribution.main[0].domain_name, "<stage 2 pending>")}",
    "Never modify the apex record for pragatinarote.com.",
  ]) : "Stage 1: add the ACM validation CNAME, then re-apply with deploy_app_services = true."
}

output "console_url" {
  description = "Public URL once DNS has propagated."
  value       = "https://${var.domain_name}"
}

# --- Debugging ------------------------------------------------------------

output "alb_dns_name" {
  description = <<-EOT
    ALB hostname, for debugging only.

    Requesting it directly returns 403: both listener rules require the
    X-Origin-Verify header that only CloudFront adds. That is the bypass
    protection working, not a misconfiguration.
  EOT
  value       = aws_lb.main.dns_name
}

output "ecs_cluster" {
  description = "Cluster name, for `aws ecs describe-services` and log inspection."
  value       = aws_ecs_cluster.main.name
}

output "log_groups" {
  description = "CloudWatch log groups, for `aws logs tail`."
  value = {
    financial_core        = aws_cloudwatch_log_group.financial_core.name
    investigation_service = aws_cloudwatch_log_group.investigation_service.name
    kafka                 = aws_cloudwatch_log_group.kafka.name
  }
}

output "github_deploy_role_arn" {
  description = "Role for GitHub Actions to assume via OIDC. Set as an Actions variable, not a secret."
  value       = aws_iam_role.github_deploy.arn
}

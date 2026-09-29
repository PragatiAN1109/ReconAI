# IAM. Least privilege, scoped to named ARNs rather than wildcards.
#
# Two kinds of role per service, and the distinction matters:
#   execution role — used by the ECS *agent* to pull images, write logs and
#                    resolve secrets BEFORE the container starts
#   task role      — used by the application *inside* the container
#
# These applications call no AWS API at all: Spring talks to PostgreSQL and
# Kafka, the Investigation Service talks to PostgreSQL, Kafka, Spring and
# Anthropic. So the task roles are created with no policies attached. That is
# deliberate, not an omission — an empty task role is the correct expression of
# "this application needs no AWS permissions", and it gives a place to attach
# one later without re-plumbing the task definitions.

# --- Execution role --------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# ECR pull + CloudWatch Logs. AWS-managed because its contents are exactly the
# agent's needs and it tracks new requirements as ECS evolves.
resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Secret resolution, scoped to the three specific secrets this stack uses.
#
# The two SSM parameters are referenced by CONSTRUCTED ARN. Terraform never
# reads their values — a `data "aws_ssm_parameter"` would pull the decrypted
# value into state, defeating the whole arrangement.
data "aws_iam_policy_document" "ecs_secrets" {
  statement {
    sid    = "ReadNamedSsmParameters"
    effect = "Allow"

    actions = [
      "ssm:GetParameters",
      "ssm:GetParameter",
    ]

    resources = [
      local.anthropic_key_arn,
      local.agent_database_url_arn,
    ]
  }

  statement {
    sid    = "ReadRdsManagedMasterSecret"
    effect = "Allow"

    actions = ["secretsmanager:GetSecretValue"]

    # Created and owned by RDS, not by Terraform. Referencing the ARN attribute
    # is safe: it is an identifier, not the credential.
    resources = [aws_db_instance.main.master_user_secret[0].secret_arn]
  }

  statement {
    sid    = "DecryptSecureStrings"
    effect = "Allow"

    actions   = ["kms:Decrypt"]
    resources = ["*"]

    # Narrows kms:Decrypt to keys used *via* SSM and Secrets Manager, which is
    # what a resource-level restriction cannot express: the AWS-managed keys
    # (aws/ssm, aws/secretsmanager) have account-specific ARNs that are awkward
    # to reference, and this condition is the idiomatic equivalent.
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"

      values = [
        "ssm.${local.region}.amazonaws.com",
        "secretsmanager.${local.region}.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role_policy" "ecs_secrets" {
  name   = "${local.name}-ecs-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_secrets.json
}

# --- Task roles ------------------------------------------------------------

resource "aws_iam_role" "financial_core_task" {
  name               = "${local.name}-financial-core-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "investigation_service_task" {
  name               = "${local.name}-investigation-service-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "kafka_task" {
  name               = "${local.name}-kafka-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# --- GitHub Actions OIDC ---------------------------------------------------
#
# No long-lived AWS access keys anywhere. GitHub presents a short-lived OIDC
# token and AWS exchanges it for temporary credentials.

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

locals {
  github_oidc_provider_arn = (
    var.create_github_oidc_provider
    ? try(aws_iam_openid_connect_provider.github[0].arn, var.existing_github_oidc_provider_arn)
    : var.existing_github_oidc_provider_arn
  )

  github_subject_prefix = "repo:${var.github_owner}/${var.github_repo}"
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to THIS repository. Without a `sub` condition, any GitHub
    # repository in the world could assume this role — the single most common
    # and most serious mistake in OIDC trust policies.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"

      values = [
        "repo:${var.github_owner}@181302119/${var.github_repo}@1389349882:ref:refs/heads/main",
        "${local.github_subject_prefix}:environment:demo",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name}-github-deploy"
  description        = "Assumed by GitHub Actions to push images and deploy the demo"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github_deploy" {
  # ECR: authorise, then push to this stack's two repositories only.
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action does not accept a resource restriction
  }

  statement {
    sid    = "EcrPush"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:DescribeImages",
    ]

    resources = [for repo in aws_ecr_repository.app : repo.arn]
  }

  # ECS: force a new deployment of the two application services.
  statement {
    sid    = "EcsDeploy"
    effect = "Allow"

    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:RegisterTaskDefinition",
      "ecs:ListTasks",
      "ecs:DescribeTasks",
    ]

    resources = ["*"] # several of these are cluster-wide read actions
  }

  # RegisterTaskDefinition embeds the task and execution role ARNs, so the
  # deploy role must be able to pass exactly those roles and nothing else.
  statement {
    sid    = "PassTaskRoles"
    effect = "Allow"

    actions = ["iam:PassRole"]

    resources = [
      aws_iam_role.ecs_execution.arn,
      aws_iam_role.financial_core_task.arn,
      aws_iam_role.investigation_service_task.arn,
      aws_iam_role.kafka_task.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # Frontend: sync the built assets and invalidate the distribution.
  statement {
    sid    = "FrontendPublish"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetObject",
    ]

    resources = [
      aws_s3_bucket.frontend.arn,
      "${aws_s3_bucket.frontend.arn}/*",
    ]
  }

  # ARN is constructed rather than referenced, so this policy does not depend
  # on the distribution existing — it is created in stage 2, and the deploy
  # role must be creatable in stage 1.
  statement {
    sid       = "CloudFrontInvalidate"
    effect    = "Allow"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = ["arn:aws:cloudfront::${local.account_id}:distribution/*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${local.name}-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

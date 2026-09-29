# Container registries.
#
# Two repositories, not three. The React console is built to static assets and
# served from S3/CloudFront, so it never becomes a container in AWS — its
# nginx image exists only for local Compose. Phase 13A's "3 repos" assumed a
# containerised frontend and is corrected here.
#
# Kafka runs the upstream apache/kafka:3.8.1 image directly rather than a
# mirrored copy. Mirroring would add a push step and storage for an image that
# is never modified. The trade-off is a dependency on Docker Hub at task start,
# including its anonymous pull rate limit; for a handful of task starts that is
# acceptable, and README notes mirroring as the fix if it ever bites.

locals {
  ecr_repositories = toset(["financial-core", "investigation-service"])
}

resource "aws_ecr_repository" "app" {
  for_each = local.ecr_repositories

  name = "${local.name}/${each.value}"

  # Without this, `terraform destroy` fails on any repository holding images —
  # which is every repository that has ever been deployed.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  # Mutable so CI can move a `latest` tag during iteration. A production
  # registry would be IMMUTABLE with digest-pinned deploys.
  image_tag_mutability = "MUTABLE"
}

# Every CI push adds an image. Without expiry the repository grows forever at
# $0.10/GB-month for layers nothing will ever pull again.
resource "aws_ecr_lifecycle_policy" "app" {
  for_each = aws_ecr_repository.app

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the 5 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = { type = "expire" }
      }
    ]
  })
}

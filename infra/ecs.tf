# ECS cluster and the two application services.
#
# Both services are gated on var.deploy_app_services. Stage 1 creates every
# task definition but runs nothing, because neither service can start before
# images exist in ECR and the human-owned secrets exist in SSM. Terraform would
# otherwise create services that crash-loop while the plan reports success.

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # per-metric charges for a stack that lives a day
  }
}

# --- Financial Core --------------------------------------------------------

resource "aws_ecs_task_definition" "financial_core" {
  family                   = "${local.name}-financial-core"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  # 0.5 vCPU / 2 GB — a valid Fargate combination (0.5 vCPU allows 1-4 GB).
  # 2 GB rather than 1: the JVM plus Hibernate plus Flyway in a 1 GB container
  # is survivable but startup-fragile, and MaxRAMPercentage=75 needs headroom.
  cpu    = 512
  memory = 2048

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.financial_core_task.arn

  container_definitions = jsonencode([
    {
      name      = "financial-core"
      image     = "${aws_ecr_repository.app["financial-core"].repository_url}:${var.image_tag}"
      essential = true

      portMappings = [{
        containerPort = 8080
        protocol      = "tcp"
      }]

      environment = [
        # Service names, never localhost.
        { name = "RECONAI_DB_URL", value = "jdbc:postgresql://${aws_db_instance.main.address}:5432/reconai" },
        { name = "RECONAI_DB_USERNAME", value = aws_db_instance.main.username },
        { name = "RECONAI_KAFKA_BOOTSTRAP_SERVERS", value = "kafka.${local.service_domain}:${local.kafka_port}" },
        { name = "RECONAI_PORT", value = "8080" },
        # The dev profile adds classpath:db/seed to the Flyway locations, which
        # is what supplies the demo fee rules the investigation depends on.
        # Seed semantics are unchanged; this only selects them.
        { name = "SPRING_PROFILES_ACTIVE", value = "dev" },
        { name = "JAVA_OPTS", value = "-XX:MaxRAMPercentage=75" },
      ]

      # The ECS agent resolves this before the container starts. The RDS-managed
      # secret is JSON; the `:password::` suffix extracts one field, so only the
      # password lands in the environment and Terraform never sees the value.
      secrets = [
        {
          name      = "RECONAI_DB_PASSWORD"
          valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::"
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.financial_core.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "financial-core"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "financial_core" {
  count = local.service_count

  name            = "${local.name}-financial-core"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.financial_core.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    assign_public_ip = true
    security_groups  = [aws_security_group.financial_core.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.financial_core.arn
    container_name   = "financial-core"
    container_port   = 8080
  }

  # Registers financial-core.reconai.local so the Investigation Service reaches
  # Spring inside the VPC instead of hairpinning out through the public ALB.
  service_registries {
    registry_arn = aws_service_discovery_service.financial_core.arn
  }

  # Flyway migration plus JVM start takes well over a minute on 0.5 vCPU. A
  # shorter grace period would have the ALB kill a task that is booting
  # correctly, producing an endless replacement loop.
  health_check_grace_period_seconds = 180

  depends_on = [
    aws_lb_listener.http,
    aws_ecs_service.kafka,
  ]

  wait_for_steady_state = false
}

# --- Investigation Service -------------------------------------------------

resource "aws_ecs_task_definition" "investigation_service" {
  family                   = "${local.name}-investigation-service"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  # 0.25 vCPU / 0.5 GB — valid (0.25 vCPU allows 0.5, 1 or 2 GB).
  cpu    = 256
  memory = 512

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.investigation_service_task.arn

  container_definitions = jsonencode([
    {
      # WHY THIS CONTAINER EXISTS
      # -------------------------
      # The consumer calls start() exactly once during FastAPI's lifespan. If
      # Kafka is unreachable at that moment the service still starts, reports
      # NOT_READY forever, and never reattaches — there is no retry loop.
      #
      # docker-compose solved this with `depends_on: kafka: service_healthy`.
      # ECS has the same primitive at container level, which is what this is:
      # the app container will not start until this one exits 0.
      #
      # Relying instead on "unhealthy target -> ECS replaces the task" would
      # converge eventually, but as a visible crash-loop that the deployment
      # circuit breaker can turn into a failed deployment.
      #
      # Fargate bills per TASK size, so this container costs nothing.
      name  = "wait-for-kafka"
      image = "public.ecr.aws/docker/library/busybox:1.36"

      # Must be non-essential: a container other containers depend on with
      # SUCCESS has to be allowed to exit without stopping the task.
      essential = false

      command = [
        "sh", "-c",
        "echo 'waiting for kafka.${local.service_domain}:${local.kafka_port}'; i=0; until nc -z kafka.${local.service_domain} ${local.kafka_port}; do i=$((i+1)); if [ $i -gt 60 ]; then echo 'kafka unreachable after 300s'; exit 1; fi; sleep 5; done; echo 'kafka reachable'"
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.investigation_service.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "wait-for-kafka"
        }
      }
    },
    {
      name      = "investigation-service"
      image     = "${aws_ecr_repository.app["investigation-service"].repository_url}:${var.image_tag}"
      essential = true

      # The ECS equivalent of docker-compose's depends_on. SUCCESS requires a
      # zero exit code, so a wait that times out prevents the app starting at
      # all rather than starting it into a broken state.
      dependsOn = [{
        containerName = "wait-for-kafka"
        condition     = "SUCCESS"
      }]

      portMappings = [{
        containerPort = 8000
        protocol      = "tcp"
      }]

      environment = [
        { name = "RECONAI_AGENT_KAFKA_BOOTSTRAP_SERVERS", value = "kafka.${local.service_domain}:${local.kafka_port}" },
        { name = "RECONAI_AGENT_FINANCIAL_CORE_BASE_URL", value = "http://financial-core.${local.service_domain}:8080" },
        { name = "RECONAI_AGENT_HOST", value = "0.0.0.0" },
        { name = "RECONAI_AGENT_PORT", value = "8000" },
        { name = "RECONAI_AGENT_ENVIRONMENT", value = "prod" },
        # Baked into the image at /srv/policies. Set explicitly because the
        # default is computed relative to the package and the directory depth
        # differs between a checkout and the image.
        { name = "RECONAI_AGENT_POLICY_CORPUS_PATH", value = "/srv/policies" },
        # Off by default. The run endpoint reports 503 and Anthropic spend is
        # structurally zero until this is deliberately changed.
        { name = "RECONAI_AGENT_LLM_PROVIDER", value = "anthropic" },
      ]

      # Both values are human-owned SecureStrings that Terraform never reads.
      # The database URL is composed rather than assembled from parts because
      # the application's config takes a single URL and ECS cannot template one
      # around a secret — which is what forces the two-stage apply.
      secrets = [
        {
          name      = "RECONAI_AGENT_DATABASE_URL"
          valueFrom = local.agent_database_url_arn
        },
        {
          name      = "RECONAI_AGENT_LLM_API_KEY"
          valueFrom = local.anthropic_key_arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.investigation_service.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "investigation-service"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "investigation_service" {
  count = local.service_count

  name            = "${local.name}-investigation-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.investigation_service.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets = aws_subnet.public[*].id
    # Also the path to api.anthropic.com when the provider is enabled.
    assign_public_ip = true
    security_groups  = [aws_security_group.investigation_service.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.investigation_service.arn
    container_name   = "investigation-service"
    container_port   = 8000
  }

  # Covers the wait-for-kafka container plus application start.
  health_check_grace_period_seconds = 180

  depends_on = [
    aws_lb_listener.http,
    aws_ecs_service.kafka,
  ]

  wait_for_steady_state = false
}

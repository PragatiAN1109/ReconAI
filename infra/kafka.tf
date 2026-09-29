# Kafka — single-broker KRaft on Fargate, reached at kafka.reconai.local:9092.
#
# Not MSK. MSK Serverless mandates IAM SASL auth, which would require new client
# libraries and configuration in BOTH applications (aws-msk-iam-auth for Spring,
# aws-msk-iam-sasl-signer-python for aiokafka) and forbids topic auto-creation,
# which this application silently depends on. MSK provisioned has a two-broker
# minimum (~$2.20/day) and takes 20-30 minutes to create and to delete. Running
# the same apache/kafka:3.8.1 image already verified locally costs ~$0.59/day,
# starts in about a minute, and requires zero application change.
#
# Accepted limitations for a deploy -> demo -> destroy lifecycle: one broker, no
# replication, ephemeral storage. A task replacement loses the topic and the
# consumer group offsets. Events published during an outage are lost. The
# consumer uses auto_offset_reset=latest, so it never expected history anyway.
#
# ON THE TOPIC-CREATION CONTAINER
# -------------------------------
# Phase 13A.1 proposed a create-topic container in this task, and the concern
# raised was whether that deadlocks. It does not, provided the dependency points
# one way only:
#
#   create-topic  dependsOn  broker: HEALTHY     <- valid
#   broker        dependsOn  create-topic        <- would deadlock; NOT done
#
# The broker has no dependency on create-topic, so it starts, becomes healthy,
# and only then is create-topic released. create-topic is non-essential and
# exits 0, which does not stop the task. Both containers share the task's
# network namespace under awsvpc, so create-topic reaches the broker on
# localhost:9092 and needs no service discovery.
#
# Keeping it in the same task is better than a separate one-off RunTask: it
# re-runs automatically every time a Kafka task starts, so a replaced broker
# regains its topic with no operator action and no external orchestration.

locals {
  kafka_port = 9092

  # Dual listeners exist for the same reason they do in docker-compose.yml: a
  # Kafka client reconnects to whatever address the broker advertises. A single
  # listener advertising localhost would send in-VPC clients back to themselves.
  #   INTERNAL  -> advertised as kafka.reconai.local, used by both applications
  #   LOCALHOST -> advertised as localhost, used only by create-topic in-task
  kafka_advertised = join(",", [
    "INTERNAL://kafka.${local.service_domain}:${local.kafka_port}",
    "LOCALHOST://localhost:29092",
  ])
}

resource "aws_ecs_task_definition" "kafka" {
  family                   = "${local.name}-kafka"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  # 512 CPU units (0.5 vCPU) with 1024 MiB is a valid Fargate combination:
  # 0.5 vCPU permits 1, 2, 3 or 4 GB.
  cpu    = 512
  memory = 1024

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.kafka_task.arn

  container_definitions = jsonencode([
    {
      name      = "kafka"
      image     = "apache/kafka:3.8.1"
      essential = true

      portMappings = [{
        containerPort = local.kafka_port
        protocol      = "tcp"
      }]

      environment = [
        { name = "KAFKA_NODE_ID", value = "1" },
        { name = "KAFKA_PROCESS_ROLES", value = "broker,controller" },
        { name = "KAFKA_LISTENERS", value = "INTERNAL://:${local.kafka_port},LOCALHOST://:29092,CONTROLLER://:9093" },
        { name = "KAFKA_ADVERTISED_LISTENERS", value = local.kafka_advertised },
        { name = "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", value = "CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,LOCALHOST:PLAINTEXT" },
        { name = "KAFKA_CONTROLLER_LISTENER_NAMES", value = "CONTROLLER" },
        { name = "KAFKA_CONTROLLER_QUORUM_VOTERS", value = "1@localhost:9093" },
        { name = "KAFKA_INTER_BROKER_LISTENER_NAME", value = "INTERNAL" },
        # Single node, so one replica is all that exists.
        { name = "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", value = "1" },
        { name = "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", value = "1" },
        { name = "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", value = "1" },
        { name = "KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS", value = "0" },
        # Retained as a fallback even though create-topic makes the topic
        # explicitly. Belt and braces: the application declares no topic in
        # code, so losing both mechanisms would break the event path silently.
        { name = "KAFKA_AUTO_CREATE_TOPICS_ENABLE", value = "true" },
      ]

      # Required for dependsOn: HEALTHY below. Uses the broker's own CLI
      # against the in-task listener, so it tests the protocol rather than
      # merely whether a port is open.
      healthCheck = {
        command     = ["CMD-SHELL", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:29092 >/dev/null 2>&1 || exit 1"]
        interval    = 15
        timeout     = 10
        retries     = 5
        startPeriod = 45
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.kafka.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "kafka"
        }
      }
    },
    {
      name = "create-topic"
      # Same image, so the Kafka CLI is already present and no second image
      # needs building or pulling.
      image = "apache/kafka:3.8.1"

      # Non-essential is mandatory here. An essential container that exits
      # stops the whole task, which would kill the broker the moment the topic
      # was created.
      essential = false

      # Waits for the broker to be HEALTHY, not merely STARTED. START would race
      # the broker's own initialisation and the create would fail.
      dependsOn = [{
        containerName = "kafka"
        condition     = "HEALTHY"
      }]

      command = [
        "/bin/sh", "-c",
        "/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:29092 --create --if-not-exists --topic reconciliation.exceptions --partitions 1 --replication-factor 1 && /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:29092 --describe --topic reconciliation.exceptions"
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.kafka.name
          "awslogs-region"        = local.region
          "awslogs-stream-prefix" = "create-topic"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "kafka" {
  count = local.service_count

  name            = "${local.name}-kafka"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.kafka.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets = aws_subnet.public[*].id
    # Public IP for ECR and CloudWatch reachability. There is no NAT Gateway;
    # inbound is closed by the security group.
    assign_public_ip = true
    security_groups  = [aws_security_group.kafka.id]
  }

  # Registers kafka.reconai.local. No load balancer: Kafka is not HTTP and is
  # never exposed publicly.
  service_registries {
    registry_arn = aws_service_discovery_service.kafka.arn
  }

  # A single broker with ephemeral state cannot be rolled. Stop the old task
  # before starting the new one rather than briefly running two brokers that
  # both claim node ID 1.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  # Off deliberately. Rollback would revert to a previous task definition,
  # which for a stateless single broker solves nothing and obscures the real
  # failure in the logs.
  deployment_circuit_breaker {
    enable   = false
    rollback = false
  }

  # Stops `terraform destroy` hanging while ECS waits for a steady state it
  # will never reach as it is being torn down.
  wait_for_steady_state = false
}

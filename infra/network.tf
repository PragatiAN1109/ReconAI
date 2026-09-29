# VPC, subnets, routing and security groups.
#
# Deliberately NO NAT Gateway. ECS tasks sit in public subnets with public IPs
# so they can reach ECR, CloudWatch and the Anthropic API directly through the
# Internet Gateway. That saves ~$1.08/day and removes a resource that is slow
# to delete.
#
# The honest trade-off: the tasks have public IP addresses. What makes that
# acceptable is that no security group admits inbound traffic from the internet
# — the app SGs accept only the ALB's SG, Kafka accepts only the two app SGs,
# and RDS accepts only the two app SGs. Outbound is open; inbound is not.
#
# This is a temporary demo, not a bank. A production build would put the tasks
# in private subnets behind a NAT Gateway or VPC endpoints.

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true # required for Cloud Map private DNS

  tags = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-igw" }
}

# --- Public subnets: ALB + ECS tasks ---------------------------------------

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.${count.index}.0/24"
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Private subnets: RDS only ---------------------------------------------
#
# No route to the internet at all. Nothing in here needs egress: RDS is reached
# only from the app security groups inside the VPC.

resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${count.index + 10}.0/24"
  availability_zone = local.azs[count.index]

  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

# A route table with no 0.0.0.0/0 entry. Local VPC routing only, which is
# stated explicitly rather than left to the default table's behaviour.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Security groups -------------------------------------------------------
#
# Rules are separate aws_vpc_security_group_*_rule resources rather than inline
# blocks. Inline rules are authoritative and fight with anything added later;
# separate rules also allow the circular references below (app <-> kafka) to
# resolve without a dependency cycle.

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "ALB. Public inbound on 80; forwards only header-verified requests."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-alb-sg" }
}

# Open on 80 because CloudFront's egress IP ranges are broad and change. The
# actual access control is the X-Origin-Verify header checked by the listener
# rules in alb.tf: without it, every request gets a 403 and no target group is
# ever reached.
#
# The CloudFront origin-facing managed prefix list was considered and rejected:
# it permits ANY CloudFront distribution, including a stranger's pointed at this
# ALB, so it does not defend against the real threat — and it consumes roughly
# 55 of the default 60 inbound rules on a security group.
resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "CloudFront to ALB (HTTP; header-verified at the listener)"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB to targets"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Financial Core --------------------------------------------------------

resource "aws_security_group" "financial_core" {
  name        = "${local.name}-financial-core"
  description = "Spring Financial Core tasks."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-financial-core-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "core_from_alb" {
  security_group_id            = aws_security_group.financial_core.id
  description                  = "ALB to Spring :8080"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

# The Investigation Service retrieves financial evidence over Spring's read-only
# HTTP API, via Cloud Map rather than the public ALB.
resource "aws_vpc_security_group_ingress_rule" "core_from_agent" {
  security_group_id            = aws_security_group.financial_core.id
  description                  = "Investigation Service to Spring :8080 (Cloud Map)"
  referenced_security_group_id = aws_security_group.investigation_service.id
  from_port                    = 8080
  to_port                      = 8080
  ip_protocol                  = "tcp"
}

# Outbound: ECR image pulls, CloudWatch Logs, RDS, Kafka. No NAT, so this
# leaves through the Internet Gateway.
resource "aws_vpc_security_group_egress_rule" "core_all" {
  security_group_id = aws_security_group.financial_core.id
  description       = "ECR, CloudWatch, RDS, Kafka"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Investigation Service -------------------------------------------------

resource "aws_security_group" "investigation_service" {
  name        = "${local.name}-investigation-service"
  description = "FastAPI Investigation Service tasks."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-investigation-service-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "agent_from_alb" {
  security_group_id            = aws_security_group.investigation_service.id
  description                  = "ALB to FastAPI :8000"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

# Outbound also carries HTTPS to api.anthropic.com when the provider is
# deliberately enabled.
resource "aws_vpc_security_group_egress_rule" "agent_all" {
  security_group_id = aws_security_group.investigation_service.id
  description       = "ECR, CloudWatch, RDS, Kafka, Anthropic API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Kafka -----------------------------------------------------------------

resource "aws_security_group" "kafka" {
  name        = "${local.name}-kafka"
  description = "Kafka broker task. Reachable only from the two application services."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-kafka-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "kafka_from_core" {
  security_group_id            = aws_security_group.kafka.id
  description                  = "Spring producer to Kafka :9092"
  referenced_security_group_id = aws_security_group.financial_core.id
  from_port                    = 9092
  to_port                      = 9092
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "kafka_from_agent" {
  security_group_id            = aws_security_group.kafka.id
  description                  = "Investigation Service consumer to Kafka :9092"
  referenced_security_group_id = aws_security_group.investigation_service.id
  from_port                    = 9092
  to_port                      = 9092
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "kafka_all" {
  security_group_id = aws_security_group.kafka.id
  description       = "ECR and CloudWatch"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- RDS -------------------------------------------------------------------

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "RDS PostgreSQL. No internet path; reachable only from the app services."
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-database-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_core" {
  security_group_id            = aws_security_group.database.id
  description                  = "Spring to PostgreSQL (public schema)"
  referenced_security_group_id = aws_security_group.financial_core.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_agent" {
  security_group_id            = aws_security_group.database.id
  description                  = "Investigation Service to PostgreSQL (investigation schema)"
  referenced_security_group_id = aws_security_group.investigation_service.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# No egress rule at all. The database initiates nothing.

# --- Service discovery -----------------------------------------------------
#
# Cloud Map private DNS, not ECS Service Connect. Service Connect injects an
# Envoy sidecar into every task, which costs CPU and memory for a routing
# feature this stack does not need.

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = local.service_domain
  description = "Private service-to-service DNS for ReconAI"
  vpc         = aws_vpc.main.id
}

resource "aws_service_discovery_service" "financial_core" {
  name = "financial-core"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_service_discovery_service" "kafka" {
  name = "kafka"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id

    dns_records {
      # Short TTL so a replaced Kafka task is picked up quickly. Clients
      # re-resolve on reconnect.
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

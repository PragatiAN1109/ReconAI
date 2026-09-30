# Application Load Balancer, with CloudFront-bypass protection.
#
# THE PROBLEM THIS SOLVES
# -----------------------
# The ALB must be internet-facing for CloudFront to reach it (CloudFront cannot
# reach an internal ALB without extra infrastructure). But every demo control —
# GET/HEAD-only on the Financial Core, path rewriting — lives in CloudFront.
# Anyone who found the *.elb.amazonaws.com name could skip all of it and POST
# directly to Spring.
#
# THE FIX
# -------
# CloudFront attaches a secret X-Origin-Verify header to every origin request.
# Both listener rules require it. The listener's DEFAULT action is a hard 403,
# so a request without the header is rejected at the load balancer and never
# reaches a target group.
#
# This secret does appear in Terraform state, and that is accepted: anything
# Terraform writes or reads lands in state, and its only power is bypassing a
# gate on synthetic demo data. It is categorically different from the Anthropic
# key or the database password, neither of which Terraform ever touches.

resource "random_password" "origin_verify" {
  length  = 48
  special = false # keeps it safe as an HTTP header value
}

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # A demo stack must not leave a load balancer behind.
  enable_deletion_protection = false

  # Investigation runs are a bounded agent loop over a language model. The
  # default 60s would cut one off mid-flight and report a gateway error for a
  # request that was working.
  idle_timeout = 300

  tags = { Name = "${local.name}-alb" }
}

# --- Target groups ---------------------------------------------------------
#
# Created unconditionally, including in stage 1 when no service is registered.
# They are free, and the listener rules need them to exist.

resource "aws_lb_target_group" "financial_core" {
  name        = "${local.name}-core"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # awsvpc tasks register by IP, not instance

  health_check {
    # The application exposes no Actuator endpoint. This is a real read-only
    # API endpoint that answers only once Flyway has migrated and the Spring
    # context is up — which is what "healthy" needs to mean.
    path                = "/api/v1/exceptions"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  # Nothing here is stateful per-connection.
  deregistration_delay = 10
}

resource "aws_lb_target_group" "investigation_service" {
  name        = "${local.name}-agent"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    # /ready, not /health. Readiness returns 503 unless BOTH Kafka and the
    # database are usable, so a task that failed to attach its consumer is
    # replaced rather than left silently not consuming. The consumer has no
    # retry loop, so this check is what makes the service self-healing.
    path                = "/ready"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  deregistration_delay = 10
}

# --- Listener --------------------------------------------------------------
#
# HTTP, not HTTPS. TLS terminates at CloudFront; this hop runs inside AWS
# between two AWS services. HTTPS here would need a second ACM certificate on
# a dedicated origin hostname (ACM will not issue for *.elb.amazonaws.com) plus
# two more manual CNAMEs at Namecheap — for a stack carrying synthetic data for
# a day. The CloudFront function strips the Authorization header after
# validating it, so no credential crosses this hop.
#
# PRODUCTION WOULD DIFFER: end-to-end TLS with an origin certificate.

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Anything that does not match a rule below — every direct hit on the ALB
  # DNS name — is refused here.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      status_code  = "403"
      message_body = "Direct access is not permitted. Requests must arrive through the ReconAI CloudFront distribution."
    }
  }
}

# Paths are matched on the BROWSER-facing prefixes (/api/core, /api/investigation)
# because the CloudFront function rewrites the URI on viewer-request, before the
# request reaches the origin. What arrives here is /api/v1/... — so the rules
# match /api/v1/* and distinguish the two services by their distinct resource
# paths. See frontend.tf for the rewrite and README.md for the full mapping.

resource "aws_lb_listener_rule" "investigation_service" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }

  condition {
    path_pattern {
      # The Investigation Service owns exactly this subtree.
      values = ["/api/v1/investigations", "/api/v1/investigations/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.investigation_service.arn
  }
}

resource "aws_lb_listener_rule" "financial_core" {
  # Lower priority than the rule above, so the more specific investigations
  # subtree is matched first and everything else under /api/v1 goes to Spring.
  listener_arn = aws_lb_listener.http.arn
  priority     = 20

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }

  condition {
    path_pattern {
      values = ["/api/v1/*"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.financial_core.arn
  }
}

# Frontend: private S3 bucket + CloudFront, with API behaviours onto the ALB.
#
# The browser only ever talks to one origin. That is what keeps requests
# same-origin and means neither backend needs CORS configured — the same
# reasoning that produced the nginx reverse proxy in the local Compose stack.
#
# The three behaviours mirror frontend/nginx.conf exactly, so the application's
# API contract is byte-identical in Vite dev, Docker Compose and AWS.

# The distribution is deliberately open to the public internet. It serves
# synthetic data only, and the demo exists to be looked at; an access prompt
# defeated that. The ALB behind it is still unreachable except through
# CloudFront, which the origin-verify header below enforces.

# --- S3 --------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  # Suffixed with the account ID because S3 bucket names are globally unique.
  bucket = "${local.name}-frontend-${local.account_id}"

  # Without this, destroy fails on any bucket containing objects — i.e. any
  # bucket that has ever been deployed to.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  # The bucket is never public. CloudFront reaches it through Origin Access
  # Control; there is no website endpoint and no public read policy.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-frontend-oac"
  description                       = "CloudFront -> private S3"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Grants read to this distribution only, not to CloudFront generally.
data "aws_iam_policy_document" "frontend_bucket" {
  count = local.service_count

  statement {
    sid    = "AllowThisDistribution"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.main[0].arn]
    }
  }
}

# Staged with the distribution, whose ARN it references. In stage 1 the bucket
# simply has no policy, which is correct: nothing is serving from it yet.
resource "aws_s3_bucket_policy" "frontend" {
  count = local.service_count

  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket[0].json
}

# --- ACM ------------------------------------------------------------------
#
# Must be us-east-1 for CloudFront, regardless of var.aws_region — hence the
# aliased provider.
#
# DNS validation with NO Route 53 record resources: Namecheap is authoritative.
# Terraform creates the certificate and outputs the CNAME to add by hand. The
# wait for issuance is a separate, stage-2 resource so that stage 1 never blocks
# on a manual step.

resource "aws_acm_certificate" "main" {
  provider = aws.us_east_1

  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cert" }
}

# Blocks until ACM observes the validation CNAME at Namecheap and issues the
# certificate. Gated into stage 2 for a hard technical reason: CloudFront
# refuses to attach a certificate that is not ISSUED, and issuance depends on a
# manual DNS record. Creating the distribution in stage 1 would therefore fail
# every time.
#
# Sequence: stage 1 creates the certificate and outputs the CNAME -> the human
# adds it at Namecheap -> stage 2 confirms issuance (fast, already valid) and
# creates the distribution.
resource "aws_acm_certificate_validation" "main" {
  count    = local.service_count
  provider = aws.us_east_1

  certificate_arn = aws_acm_certificate.main.arn

  timeouts {
    # Generous: DNS propagation at an external registrar is not under our
    # control. If this times out, the record is wrong or not yet visible.
    create = "45m"
  }
}

# --- CloudFront -----------------------------------------------------------

resource "aws_cloudfront_function" "viewer_request" {
  name    = "${local.name}-viewer-request"
  runtime = "cloudfront-js-2.0"
  comment = "/api prefix rewriting and SPA history fallback"
  publish = true

  code = file("${path.module}/functions/viewer-request.js")
}

# Managed policies, referenced by name rather than hardcoded ID.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  # Forwards query strings, cookies and headers to the origin but rewrites Host
  # to the origin's own hostname, which ALB host-based routing requires.
  name = "Managed-AllViewerExceptHostHeader"
}

locals {
  s3_origin_id  = "s3-frontend"
  alb_origin_id = "alb-api"
}

resource "aws_cloudfront_distribution" "main" {
  count = local.service_count

  # The certificate must be ISSUED first; see aws_acm_certificate_validation.
  depends_on = [aws_acm_certificate_validation.main]

  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name} operations console"
  price_class     = "PriceClass_100" # NA + EU; cheapest, and the demo audience is here

  aliases = [var.domain_name]

  # Single-page app: index.html is the only document.
  default_root_object = "index.html"

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id   = local.alb_origin_id
    domain_name = aws_lb.main.dns_name

    custom_origin_config {
      http_port  = 80
      https_port = 443
      # HTTP to the origin. See the rationale in alb.tf.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }

    # THE ALB BYPASS CONTROL. Both ALB listener rules require this header, and
    # the listener's default action is 403. CloudFront adds it on every origin
    # request; a client hitting the ALB directly cannot supply it.
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  # --- default behaviour: the React app ---
  default_cache_behavior {
    target_origin_id       = local.s3_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.viewer_request.arn
    }
  }

  # --- Financial Core: READ ONLY ---
  #
  # GET and HEAD only, which costs nothing functionally: the console is already
  # read-only against the Financial Core (frontend/src/api/financialCore.ts
  # imports only getOptional). It removes the ability to create transactions,
  # settlements or trigger reconciliation through the browser, which is the
  # cheapest demo-safety control available and matches the product's own
  # boundary — the UI never writes to the financial core.
  ordered_cache_behavior {
    path_pattern             = "/api/core/*"
    target_origin_id         = local.alb_origin_id
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    compress                 = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.viewer_request.arn
    }
  }

  # --- Investigation Service: needs POST for approve/reject/escalate ---
  ordered_cache_behavior {
    path_pattern             = "/api/investigation/*"
    target_origin_id         = local.alb_origin_id
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    compress                 = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.viewer_request.arn
    }
  }

  # NO custom_error_response here, deliberately.
  #
  # It is a distribution-WIDE setting — it cannot be scoped to a cache
  # behaviour. Mapping 403/404 -> /index.html with a 200 also rewrote genuine
  # API errors coming back from the ALB origin: a correct 404 from
  # /api/investigation/investigations/{id}/recommendation arrived at the
  # browser as 200 text/html and broke JSON parsing in the console.
  #
  # SPA history fallback now lives in the viewer-request function, which can
  # tell the two path spaces apart. See functions/viewer-request.js.

  viewer_certificate {
    acm_certificate_arn = aws_acm_certificate.main.arn
    ssl_support_method  = "sni-only"
    # TLS 1.2 minimum. Anything older is not worth supporting.
    minimum_protocol_version = "TLSv1.2_2021"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # After this is created, add the final CNAME at Namecheap:
  #   reconai -> <domain_name output>
  # See the dns_setup output and README.md.
}

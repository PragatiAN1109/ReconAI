# ReconAI — AWS demo deployment

Terraform for a short-lived AWS deployment of ReconAI, built for one lifecycle:

```
apply (stage 1) → push images → create secrets → apply (stage 2)
→ verify → record demo → screenshots → destroy
```

Expected cost is **~$0.12/hour**, about **$2.85 for a full day**. It is designed
to be destroyed cleanly afterwards.

> **Nothing in this directory has been applied.** No AWS resource exists yet.
> Read this file before running anything.

---

## 1. Architecture

```
                         ┌── EXTERNAL ──────────────────┐
                         │ api.anthropic.com (opt-in)   │
                         └──────────▲───────────────────┘
  Browser                           │ HTTPS via IGW
     │ https://reconai.pragatinarote.com
     ▼
┌──────────────────────────────────────────┐   CNAME  ┌───────────┐
│ CloudFront + ACM                         │◄─────────│ Namecheap │
│ ONE viewer-request function:             │          └───────────┘
│  · /api/core → /api/v1                   │
│  · /api/investigation → /api/v1          │
│  · SPA fallback (non-API only)           │
│ origin header: X-Origin-Verify           │
│ /api/core/* = GET,HEAD only              │
│ /api/core/demo/reconcile = +POST (exact) │
└───┬──────────────────────────┬───────────┘
    │ default                  │ /api/*
    ▼                          ▼
┌─────────┐          ┌───────────────────────────────┐
│   S3    │          │ ALB (internet-facing)         │
│ private │          │ header + path → target group  │
│  + OAC  │          │ DEFAULT → 403                 │◄── direct hits die here
└─────────┘          └────┬─────────────────┬────────┘
                          ▼                 ▼
          ┌───────────────────┐   ┌──────────────────────────┐
          │ ECS Spring :8080  │◄──│ ECS FastAPI :8000        │
          │ 0.5 vCPU / 2 GB   │   │ 0.25 vCPU / 0.5 GB       │
          │                   │   │ + init: wait-for-kafka   │
          └──┬─────────┬──────┘   └──┬──────────────┬────────┘
             │ Cloud Map: financial-core.reconai.local
             │         ▼ produce      ▼ consume     │
             │  ┌──────────────────────────────┐    │
             │  │ ECS Kafka 3.8.1 KRaft :9092  │    │
             │  │ + init: create-topic         │    │
             │  └──────────────────────────────┘    │
             └─────────────┬────────────────────────┘
                           ▼
              ┌──────────────────────────────┐
              │ RDS PostgreSQL 16 (private)  │
              │ public → Spring              │
              │ investigation → Python       │
              └──────────────────────────────┘
```

No NAT Gateway, no MSK, no Route 53 zone, no WAF, no API Gateway.

**Production would differ in two documented ways:** CloudFront→ALB uses HTTP
here (TLS terminates at CloudFront), and ECS tasks run in public subnets with
security groups as the only inbound control. Both are deliberate cost/complexity
trades for a temporary synthetic-data demo.

---

## 2. Prerequisites

| | |
|---|---|
| Terraform | **≥ 1.10** (native S3 state locking; no DynamoDB table) |
| AWS CLI | v2, with credentials configured |
| Docker | to build and push the two service images |
| Namecheap access | to add **two** CNAME records |

**Enable billing alerts first.** In the Billing console →
*Billing preferences* → *Receive Billing Alerts*. This is an account preference,
not an API-creatable resource, which is why Terraform does not create the alarm
(see `observability.tf`). Then add a CloudWatch alarm on
`AWS/Billing EstimatedCharges` in **us-east-1** at a threshold you're happy with
— $10 is ample here.

---

## 3. Variables to review

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` is gitignored. **It needs no secrets** — review
`domain_name`, `github_owner`, `github_repo`, and leave
`deploy_app_services = false`.

If this AWS account already has a GitHub OIDC provider (an account may hold only
one for `token.actions.githubusercontent.com`), set
`create_github_oidc_provider = false` and supply
`existing_github_oidc_provider_arn`.

---

## 4. AWS credentials

```bash
aws configure
```

```bash
aws sts get-caller-identity
```

---

## 5. State backend

Run once. Creates only the state bucket, using local state, in its own root
module so that destroying the demo can never destroy the state.

```bash
cd infra/bootstrap && terraform init
```

```bash
terraform apply -var state_bucket_name=reconai-tfstate-CHANGE-ME
```

S3 bucket names are global — suffix it with something account-specific. Then
copy the `backend_block` output into `infra/backend.tf` and initialise:

```bash
cd infra && terraform init
```

---

## 6. Stage 1 — foundation

Creates the VPC, RDS, ECR, ALB, S3, ACM certificate, Cloud Map, IAM and log
groups. **No ECS task runs and no CloudFront distribution is created**, because
neither can work before images and secrets exist.

```bash
cd infra && terraform plan
```

**Review the plan before applying.** Then:

```bash
terraform apply
```

### 6a. ACM validation CNAME

```bash
terraform output acm_validation_record
```

Add that CNAME at Namecheap. Namecheap strips the zone suffix, so enter only
the host portion — everything before `.pragatinarote.com`. Issuance usually
takes 5–15 minutes.

> **Never modify the apex record for `pragatinarote.com`.** It is live.

### 6b. Push the images

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com"
```

Both images must be built for **linux/amd64** — Fargate's default architecture.
On an Apple Silicon machine the local Compose images are arm64 and will not run.

```bash
CORE=$(terraform output -json ecr_repositories | python3 -c 'import json,sys;print(json.load(sys.stdin)["financial-core"])') && docker buildx build --platform linux/amd64 -t "$CORE:latest" --push ../backend
```

```bash
AGENT=$(terraform output -json ecr_repositories | python3 -c 'import json,sys;print(json.load(sys.stdin)["investigation-service"])') && docker buildx build --platform linux/amd64 -f ../agent-service/Dockerfile -t "$AGENT:latest" --push ..
```

Note the Investigation Service build context is the **repository root** (`..`),
because its image carries the policy corpus from `policies/`.

---

## 7. Secrets — created by you, never by Terraform

Terraform owns neither of these values and never reads them. It holds only the
parameter names and the derived ARNs; the ECS agent resolves the values at task
start.

This is not stylistic. `aws_ssm_parameter` writes its value into Terraform
state, and `data "aws_ssm_parameter"` reads the decrypted value into state —
`sensitive = true` hides CLI output only, and `ignore_changes` does not stop a
refresh from pulling the real value in. The only way to keep a value out of
state is for Terraform never to touch it.

### 7a. Anthropic API key (optional)

Only needed for a **live** investigation. Leave it out and the stack runs with
the provider disabled; the run endpoint returns 503 and Anthropic spend is
structurally zero.

```bash
aws ssm put-parameter --name /reconai/demo/anthropic-api-key --type SecureString --value '<ANTHROPIC_API_KEY>' --region us-east-1
```

Even with the key present, the task sets `RECONAI_AGENT_LLM_PROVIDER=none`.
Enabling a live provider is a deliberate task-definition change.

### 7b. Investigation Service database URL (required)

The application's config takes one composed URL and ECS cannot template a URL
around a secret, so it must be assembled once by hand. This is the reason for
the two-stage apply.

```bash
terraform output db_endpoint && terraform output -raw db_master_secret_arn
```

```bash
aws secretsmanager get-secret-value --secret-id "$(terraform output -raw db_master_secret_arn)" --query SecretString --output text --region us-east-1
```

That prints JSON containing the password. Compose the URL using
`terraform output agent_database_url_template` as the shape, then:

```bash
aws ssm put-parameter --name /reconai/demo/agent-database-url --type SecureString --value 'postgresql+asyncpg://reconai:<DB_PASSWORD>@<DB_ENDPOINT>:5432/reconai' --region us-east-1
```

Do not paste the assembled URL into any file.

---

## 8. Stage 2 — CloudFront and the services

Only after: images pushed, both SSM parameters created, ACM certificate
**ISSUED**.

```bash
aws acm list-certificates --region us-east-1 --query "CertificateSummaryList[?DomainName=='reconai.pragatinarote.com']"
```

Set `deploy_app_services = true` in `terraform.tfvars`, then:

```bash
terraform plan
```

```bash
terraform apply
```

### 8a. Final DNS record

```bash
terraform output cloudfront_domain
```

Add at Namecheap: `CNAME  reconai  →  <that value>`. The distribution takes
15–20 minutes to deploy globally.

---

## 9. Validation gates

Run these in order. Each proves something specific.

```bash
aws ecs describe-services --cluster "$(terraform output -raw ecs_cluster)" --services reconai-demo-kafka reconai-demo-financial-core reconai-demo-investigation-service --query "services[].{name:serviceName,running:runningCount,desired:desiredCount}"
```

**Financial Core** — answers only after Flyway has migrated:

```bash
curl -sS "https://$(terraform output -raw console_url | sed 's#https://##')/api/core/exceptions"
```

**Investigation Service readiness — the hard gate:**

```bash
aws logs tail "$(terraform output -json log_groups | python3 -c 'import json,sys;print(json.load(sys.stdin)["investigation_service"])')" --since 10m
```

`/ready` must report `"kafka_consumer":"RUNNING"`.

> ### Demo-event safety rule
>
> **Do not create any transaction or run reconciliation until
> `kafka_consumer` reads `RUNNING`.**
>
> The consumer uses `auto_offset_reset=latest`. If Spring publishes before the
> consumer has attached, the consumer starts at the end of the topic and the
> event is **silently lost** — no error, just a missing investigation. This is
> the single most likely way to waste a demo recording.

**ALB bypass protection** — this *should* return 403:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "http://$(terraform output -raw alb_dns_name)/api/v1/exceptions"
```

---

## 10. Kafka behaviour

**Topic creation.** A non-essential `create-topic` container in the Kafka task
waits for the broker to report `HEALTHY`, then runs
`kafka-topics.sh --create --if-not-exists`. The dependency points one way only
— create-topic depends on the broker, never the reverse — so there is no
deadlock. Keeping it in the same task means a replaced broker regains its topic
automatically with no operator action. `KAFKA_AUTO_CREATE_TOPICS_ENABLE=true` is
retained as a fallback because the application declares no topic in code.

**FastAPI startup.** The consumer calls `start()` once; if Kafka is unreachable
at that moment the service reports `NOT_READY` forever with no reattachment. A
`wait-for-kafka` init container blocks the app container until port 9092 answers,
using ECS `dependsOn: SUCCESS` — the direct equivalent of Compose's
`depends_on: service_healthy`. Fargate bills per task, so it costs nothing.

**If Kafka restarts later.** Ephemeral storage, so the topic and consumer
offsets are lost and the new task recreates the topic. The already-started
consumer reconnects on its own, so `/ready` stays `RUNNING` and ECS does not
replace the FastAPI task. Events published during the gap are lost — accepted
for this lifecycle.

---

## 11. Cost

| | Estimate |
|---|---|
| Hourly | ~$0.12 |
| 6 hours | ~$0.72 |
| 12 hours | ~$1.43 |
| 24 hours | ~$2.85 |
| **Accidental month** | **~$86** |

Estimates, not verified live pricing. Dominated by Fargate (~$0.066/hr), ALB
(~$0.031/hr) and RDS (~$0.019/hr). CloudFront falls inside the free tier at
demo volumes; SSM Parameter Store Standard is free.

**Biggest avoidable trap:** swapping Kafka for MSK Serverless would add roughly
**$540/month**.

---

## 12. Destroy

```bash
cd infra && terraform destroy
```

Allow **15–25 minutes** — most of it CloudFront disabling and deleting. Do not
interrupt it; a partial teardown still bills.

### Verify nothing bills

```bash
aws ecs list-clusters --query 'clusterArns' && aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' && aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName'
```

```bash
aws cloudfront list-distributions --query 'DistributionList.Items[].DomainName' && aws logs describe-log-groups --log-group-name-prefix /ecs/reconai --query 'logGroups[].logGroupName'
```

All should be empty of `reconai` resources.

### Intentionally surviving

| Resource | Why | Remove with |
|---|---|---|
| Terraform state bucket | Separate root module, `prevent_destroy` | Empty it, remove `prevent_destroy`, then `terraform destroy` in `infra/bootstrap` |
| Two SSM SecureStrings | Human-owned; re-apply reuses them | `aws ssm delete-parameter --name /reconai/demo/anthropic-api-key` (and the other) |
| Two Namecheap CNAMEs | Outside AWS and outside Terraform | Delete in the Namecheap dashboard |
| ECR images | Repositories use `force_delete`, so they go with the stack | — |

Nothing else should survive. The RDS-managed master secret is deleted by RDS
with the instance.

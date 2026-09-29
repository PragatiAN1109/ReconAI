# RDS PostgreSQL 16.
#
# One database, two schema owners — unchanged from local:
#   public        -> Spring Financial Core
#   investigation -> Python Investigation Service
#
# No foreign keys cross the boundary and Python never queries Spring's tables,
# so splitting into two instances would double the cost while enforcing nothing
# that is not already enforced.
#
# `manage_master_user_password = true` is the important line. RDS generates the
# password and stores it in an AWS-managed Secrets Manager secret, so Terraform
# never receives it and it never enters state. The alternative —
# `random_password` plus the `password` argument — writes the credential into
# state in plaintext, which the secret-ownership boundary forbids.

resource "aws_db_subnet_group" "main" {
  name        = "${local.name}-db"
  description = "Private subnets for RDS"
  subnet_ids  = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier = "${local.name}-postgres"

  engine = "postgres"
  # Major version only: RDS selects the current minor release and
  # auto_minor_version_upgrade keeps it patched. Pinning a minor version is a
  # recurring maintenance chore for no benefit here.
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "reconai"
  username = "reconai"

  # Terraform never sees the password. Spring reads it from the managed secret
  # via ECS secret injection; see ecs.tf.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]

  # Private subnets with no internet route, and no public endpoint. The only
  # paths in are the two application security groups.
  publicly_accessible = false
  multi_az            = false # a demo does not need HA, and it doubles the cost

  # --- Destroyability -----------------------------------------------------
  # This stack exists to be applied, demonstrated and destroyed. Each of these
  # would otherwise leave a billable artefact or block `terraform destroy`
  # outright.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false

  # Off because there is no maintenance window worth defending on a resource
  # that lives for a day, and a mid-demo restart is worse than a missed patch.
  auto_minor_version_upgrade = false
  apply_immediately          = true

  # Free tier of Performance Insights is 7 days on t-class; enabling it costs
  # nothing but adds a resource to reason about at destroy. Left off.
  performance_insights_enabled = false

  tags = { Name = "${local.name}-postgres" }
}

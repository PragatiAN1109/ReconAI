#!/bin/sh
# Bring the investigation schema up to date, then start the service.
#
# `alembic upgrade head` is idempotent: it applies only revisions the target
# database has not recorded, and is a no-op against an already-current schema.
# It creates and alters; it does not drop, truncate or reset. Running it against
# a database holding existing demo data is safe, and that is deliberate — this
# stack is expected to start against a volume that already has data in it.
set -e

echo "[entrypoint] Applying investigation schema migrations..."
alembic upgrade head
echo "[entrypoint] Migrations up to date."

exec "$@"

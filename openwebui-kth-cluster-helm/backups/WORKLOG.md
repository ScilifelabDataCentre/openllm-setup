# Worklog

- Backup setup was first created for `dev-2`, then adapted for `prod-2`.
- Added `namespace: openllm` in `kustomization.yaml` so the backup resources target the shared Open WebUI namespace.
- Fixed the kustomize resource entry to use `backup-cronjob.yaml` with the correct filename casing.
- Updated backup and restore jobs to use the same PostgreSQL connection details as the Open WebUI deployment:
  `PGHOST=open-webui-postgresql`, `PGUSER=openwebui`, `PGDATABASE=openwebui`, and password from secret `open-webui-postgresql` key `password`.
- Updated the backup and restore client images from PostgreSQL 16 to PostgreSQL 18 after `pg_dump` failed against PostgreSQL `18.4` with a server/client version mismatch.
- Changed the restore job from `/bin/bash` to `/bin/sh -ec` so it works with the Alpine PostgreSQL image.
- Scoped `NetworkPolicy.yaml` to the PostgreSQL primary pod instead of all pods in `openllm`.
- Added `backup-pvc-shell-pod.yaml` as an on-demand helper pod for mounting and inspecting the backup PVC.

## Review — 2026-10-09

- Corrected the Trident Application label selector from `storage: app-backup` to `storage: postgresql-backup`, matching the backup PVC. This ensures the PVC is included in scheduled protection.
- Updated `README.md` to describe the Trident Protect `Application` and `Schedule` created by the kustomization, replacing the obsolete Rubrik `ProtectionSet` reference.
- Updated the backup CronJob comment to describe the configured Trident schedule at 01:23.
- Migrated the backup PVC, ConfigMap, CronJobs, NetworkPolicy, and Trident Protect resources into Helm templates. The helper pod remains an on-demand manifest outside the release.
- For the existing ArgoCD deployment, migrate the same-named resources with a normal sync; do not use `Replace` or `Force`. `helm --take-ownership` applies only to direct Helm CLI deployments.
- **Operational follow-up:** test a restore in an isolated namespace or a maintenance window before depending on it for recovery. The suspended restore CronJob uses `pg_restore --clean` against the live PostgreSQL service, which can conflict with connected application clients.

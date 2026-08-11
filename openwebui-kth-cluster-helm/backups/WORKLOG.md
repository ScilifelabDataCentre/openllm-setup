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

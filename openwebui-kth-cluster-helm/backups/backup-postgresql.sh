#!/usr/bin/env bash

set -eu

declare backup_dir backup_file deleted_files
backup_dir="${BACKUP_DIR:-/backups}"
backup_file="${backup_dir}/postgresql-$(date +%s).pgdump"

# Fake the real commands if --dry-run is specified
declare -a pg_dump_cmd=(pg_dump)
declare -a find_actions=(-delete -print)
if [[ "${1:-}" == "--dry-run"  ]]; then
  pg_dump_cmd=(echo  "[backup_postgres]" pg_dump)
  find_actions=(-print)
  printf "[backup_postgres] Dry run\n"
fi

mkdir -vp "${backup_dir}"

printf "[backup_postgres] Backing up database %s to %s\n" "${PGDATABASE}" "${backup_file}"

if "${pg_dump_cmd[@]}" --clean --if-exists -F c -b -f "${backup_file}"; then
  deleted_files=$(find "${backup_dir}" -type f -name "*.pgdump" -mtime +7 "${find_actions[@]}")
  printf "[backup_postgres] Removed backups older than a week: %s\n" "${deleted_files:-(none)}"
fi
#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "usage: $0 BACKUP_DESTINATION" >&2
  exit 2
fi
backup_destination=$1
if [[ $backup_destination == *$'\n'* || $backup_destination == *$'\r'* ]]; then
  echo "backup destination contains unsupported control characters" >&2
  exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ $backup_destination != /* ]]; then
  backup_destination="$PWD/$backup_destination"
fi
uv_bin=$(command -v uv) || { echo "uv not found" >&2; exit 2; }
if [[ $root == *%* || $backup_destination == *%* || $uv_bin == *%* ]]; then
  echo "cron paths containing % are unsupported" >&2
  exit 2
fi
printf -v root_q '%q' "$root"
printf -v backup_q '%q' "$backup_destination"
printf -v uv_q '%q' "$uv_bin"

prefix="cd $root_q && $uv_q run python -m"
existing=$(crontab -l 2>/dev/null || true)
filtered=$(printf '%s\n' "$existing" | grep -v 'day-trading-engine-local-' || true)
{
  printf '%s\n' "$filtered"
  printf 'CRON_TZ=America/Edmonton # day-trading-engine-local-timezone\n'
  printf '0 6 * * * %s day_trading_engine.ops.scheduled quality # day-trading-engine-local-quality\n' "$prefix"
  printf '15 6 * * * %s day_trading_engine.ops.scheduled history # day-trading-engine-local-history\n' "$prefix"
  printf '0 6 * * * cd %s && timeout 13h %s run python -m day_trading_engine.engine.live # day-trading-engine-local-scan\n' "$root_q" "$uv_q"
  printf '25 18 * * * %s day_trading_engine.ops.scheduled after-close # day-trading-engine-local-close\n' "$prefix"
  printf '15 19 * * * %s day_trading_engine.ops.scheduled monthly-report # day-trading-engine-local-monthly-report\n' "$prefix"
  printf '30 19 * * * %s day_trading_engine.ops.scheduled backup %s # day-trading-engine-local-backup\n' "$prefix" "$backup_q"
  printf '45 19 * * * %s day_trading_engine.ops.scheduled snapshot %s # day-trading-engine-local-snapshot\n' "$prefix" "$backup_q"
} | crontab -
echo "Scheduled local day-trading workflow jobs."

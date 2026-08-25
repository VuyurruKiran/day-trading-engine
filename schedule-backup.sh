#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "usage: $0 DESTINATION [HH:MM]" >&2
  exit 2
fi
destination=$1
time=${2:-18:30}
if [[ ! $time =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "time must use HH:MM" >&2
  exit 2
fi
if [[ $destination == *$'\n'* || $destination == *$'\r'* ]]; then
  echo "destination contains unsupported control characters" >&2
  exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backup="$root/backup.sh"
if [[ $destination != /* ]]; then
  destination="$PWD/$destination"
fi
printf -v backup_q '%q' "$backup"
printf -v destination_q '%q' "$destination"
minute=${time#*:}
hour=${time%:*}
command="$backup_q $destination_q"
command=${command//%/\\%}
existing=$(crontab -l 2>/dev/null || true)
filtered=$(printf '%s\n' "$existing" | grep -v 'day-trading-engine-daily-backup' || true)
{
  printf '%s\n' "$filtered"
  printf '%s %s * * * %s # day-trading-engine-daily-backup\n' "$minute" "$hour" "$command"
} | crontab -
echo "Scheduled daily backup at $time"

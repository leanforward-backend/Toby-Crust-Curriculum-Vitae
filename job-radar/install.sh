#!/usr/bin/env bash
# Install the Job Radar as systemd user units: a daily 6:50am scan and the dashboard on
# http://127.0.0.1:8787. Run ./install.sh --uninstall to remove them.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now job-radar-scan.timer job-radar-web.service 2>/dev/null || true
  rm -f "$UNITS"/job-radar-scan.service "$UNITS"/job-radar-scan.timer "$UNITS"/job-radar-web.service
  systemctl --user daemon-reload
  echo "Job Radar units removed. Your data in $DIR/data was left alone."
  exit 0
fi

mkdir -p "$UNITS" "$DIR/data"
for unit in job-radar-scan.service job-radar-scan.timer job-radar-web.service; do
  sed "s|@DIR@|$DIR|g" "$DIR/systemd/$unit" > "$UNITS/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now job-radar-scan.timer job-radar-web.service
echo "Dashboard: http://127.0.0.1:8787"
systemctl --user list-timers job-radar-scan.timer --no-pager

#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run sudo ./install.sh' >&2;exit 1;fi
HERE="$(cd "$(dirname "$0")" && pwd)"
id jrule >/dev/null 2>&1 || useradd --system --home-dir /opt/jrule --shell /usr/sbin/nologin jrule
install -d -m 0755 /opt/jrule /opt/jrule/app /opt/jrule/app/static /opt/jrule/scripts /etc/jrule
install -d -o jrule -g jrule -m 0750 /var/lib/jrule

# The preferred deployment is a Git checkout directly at /opt/jrule. In that
# case the source and destination are the same tree, so do not copy files over
# themselves. Package/extracted installs from another directory still work.
if [[ "$HERE" != "/opt/jrule" ]]; then
  cp -a "$HERE/app/." /opt/jrule/app/
  install -m 0755 "$HERE/scripts/load-japi-key.sh" /opt/jrule/scripts/load-japi-key.sh
  install -m 0755 "$HERE/scripts/jrule-watchdog.py" /opt/jrule/scripts/jrule-watchdog.py
  install -m 0644 "$HERE/requirements.txt" /opt/jrule/requirements.txt
fi
chmod 0755 /opt/jrule/scripts/jrule-watchdog.py
if [[ ! -f /etc/jrule/jrule.env ]];then
  install -m 0600 /dev/null /etc/jrule/jrule.env
  cat >/etc/jrule/jrule.env <<'EOF'
JRULE_DB=/var/lib/jrule/jrule.db
JRULE_BIND=127.0.0.1
JRULE_PORT=8096
JRULE_ENABLE_ACTIONS=0
# JRULE_ADMIN_TOKEN=<long-random-token>
# jAPI key is read from /etc/japi/api.env by a root-only systemd pre-start hook.
JRULE_JAPI_BASE_URL=http://127.0.0.1:8088
EOF
fi
chmod 0600 /etc/jrule/jrule.env
if [[ ! -x /opt/jrule/venv/bin/python ]];then python3 -m venv /opt/jrule/venv;fi
/opt/jrule/venv/bin/python -m pip install -r /opt/jrule/requirements.txt
# Keep the checkout writable by its deploying user (e.g. grayjo on ESC-JAPPS).
# Application code is read-only to the jrule service through systemd; runtime
# data and credentials retain their separate protected ownership/permissions.
DEPLOY_USER="${JRULE_DEPLOY_USER:-${SUDO_USER:-}}"
if [[ -n "$DEPLOY_USER" && "$DEPLOY_USER" != root ]]; then
  id "$DEPLOY_USER" >/dev/null
  chown -R "$DEPLOY_USER:$(id -gn "$DEPLOY_USER")" /opt/jrule
fi
install -m 0644 "$HERE/systemd/jrule.service" /etc/systemd/system/jrule.service
install -m 0644 "$HERE/systemd/jrule-watchdog.service" /etc/systemd/system/jrule-watchdog.service
install -m 0644 "$HERE/systemd/jrule-watchdog.timer" /etc/systemd/system/jrule-watchdog.timer
systemctl daemon-reload
systemctl enable jrule.service
systemctl restart jrule.service
systemctl enable --now jrule-watchdog.timer
echo 'jRULE installed. Test: curl http://127.0.0.1:8096/api/version (or configured bind address)'

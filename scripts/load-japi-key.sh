#!/usr/bin/env bash
set -euo pipefail
# Run as root via systemd ExecStartPre=+. Extract only the jAPI key,
# never load SQL credentials into the jRULE process environment.
source_file=/etc/japi/api.env
runtime_file=/run/jrule/japi-api-key
key="$(sed -n 's/^API_KEY=//p' "$source_file" | head -n 1)"
if [[ -z "$key" ]]; then
  echo 'jAPI API_KEY is missing' >&2
  exit 1
fi
umask 077
printf '%s\n' "$key" > "$runtime_file"
chown jrule:jrule "$runtime_file"
chmod 0600 "$runtime_file"
# jRULE v0.6: jAPI rules engine

A small self-hosted rules engine that observes **named jAPI GET sources** and runs **configured jAPI POST actions**. It is independent of Tapo Rules.

- Deploys to `/opt/jrule` via `jrule.service`; SQLite lives in `/var/lib/jrule/jrule.db`.
- Each source needs a **name**, a **relative jAPI endpoint** (query strings supported, e.g. `/v1/mdserver/workstations?include_users=true`) and optionally a polling interval and derived property mapping.
- Each POST action needs a name, relative endpoint and JSON body template.
- All sources publish into one shared global property pool. A poll advances `last`/`current` only for properties included in that poll.
- Rules run on a configurable schedule and can inspect any `current.<object>.<property>`, `previous.<object>.<property>`, or `meta.<object>.<property>.last_changed`.
- jRULE uses the jAPI `X-API-Key` automatically. No per-source or per-action credential configuration, SQL access, or arbitrary target URLs.

## Install or upgrade on esc-japps

```bash
git clone https://github.com/ProfessorOfJankology/jRULE.git
cd jRULE
sudo bash install.sh
sudo systemctl status jrule --no-pager
```

The installer preserves `/etc/jrule/jrule.env`, the database and existing jRULE rules. Back up your database before upgrading. Existing v0.2 entries using full HTTP URLs are **not valid jAPI endpoints**; update their endpoint paths in the UI before enabling their polls/actions. Old v0.2 sources stored as `http.get` objects are not scheduled by v0.3; delete/recreate them or use a fresh database if desired.

### Automatic jAPI authentication

The systemd service runs `/opt/jrule/scripts/load-japi-key.sh` as a privileged **pre-start hook**. It extracts only `API_KEY` from `/etc/japi/api.env` into `/run/jrule/japi-api-key`, owner `jrule`, mode `0600`. No SQL credentials enter the jRULE environment. The runtime file is recreated whenever jRULE restarts, so a rotated key requires `sudo systemctl restart jrule`. The jAPI base address defaults to `http://127.0.0.1:8088`; override `JRULE_JAPI_BASE_URL` in `/etc/jrule/jrule.env` if needed. Keep `JRULE_ENABLE_ACTIONS=0` while validating sources and actions.

If jAPI isn't local, the base URL must be explicitly configured server-side; the browser cannot set arbitrary destinations.

### Set administrator token

Set `JRULE_ADMIN_TOKEN` to a long random secret in `/etc/jrule/jrule.env` and restart the service. Supply it in jRULE's Settings tab to create, edit, delete, or manually poll. Avoid exposing the HTTP UI over untrusted networks. Read-only `/api/objects` exposes source data to LAN clients without authentication: keep on a trusted LAN or add reverse-proxy authentication.

## Configure a source

- Name: `presence`
- Endpoint: `/v1/mdserver/sessions`
- Poll interval: 30 seconds
- Mapping: `{}` (import top-level response fields; only names matching `[A-Za-z][A-Za-z0-9_-]*` are supported)

Optional mappings can be plain dotted paths or derived mapping objects. Derived mappings support `value`, `first`, `count`, `exists`, `contains`, and `equals`, plus a default used when a path disappears. A failed request updates source health but **does not** advance properties.

Example presence source:

- Name: `presence`
- Endpoint: `/v1/mdserver/workstations?include_users=true`
- Interval: 30 seconds
- Derived mapping for ESC-R1:

```json
{
  "current_user": {
    "path": "workstation_users.ESC-R1.0",
    "op": "value",
    "default": null
  },
  "present": {
    "path": "connected_workstations",
    "op": "contains",
    "value": "ESC-R1",
    "default": false
  }
}
```

When ESC-R1 disappears from the response, `current_user` advances to `null` and `present` advances to `false`, so rules can detect logout/disconnect using current/previous values.

## Rule value expressions

Rule conditions can transform a parameter before comparison. The visual rule builder supports collection and conversion steps such as `count`, `first`, `last`, dictionary `key`, list `index`, `as number`, `as text`, `as true/false`, `as date`, `as time`, lowercase and uppercase. The common comparison operators then work on the transformed value.

Lists are treated as collection values during source-field discovery. Numeric list positions such as `.0`, `.1` and `.2` are not persisted as discovered fields because their meaning changes when list ordering changes. Stable dictionary keys may still be discovered. Legacy rules and derived mappings that explicitly use numeric JSON paths continue to work.

Structured expressions are stored in rule JSON as, for example:

```json
{
  "source": "current.Presence.connected_workstations",
  "transforms": [
    { "op": "count" }
  ]
}
```

### Typed rule comparison values

The rule builder's comparison-value dropdown supports String, Number, Boolean, Null, Parameter, Literal (unconverted), and JSON (advanced), matching the action editor's value types. String and Literal use text exactly as entered; quotes are not needed. Parameter resolves a jRULE expression. Explicit values save as `right_type: "typed"` and retain their JSON type during evaluation, so e.g. the string `"00123"` does not become the number `123`. Existing rules without that marker still use the original automatic scalar parsing for compatibility.

### Recursive collection conditions

The visual rule builder also supports `contains (recursive)` and `does not contain (recursive)`. These search all descendant **values** of dictionaries and lists for an exact match, independent of nesting depth. They do not match dictionary keys or string substrings. Existing `contains` / `does not contain` remain unchanged.

For example, a presence rule can use `current.Presence.workstation_users` with operator `contains_recursive` and literal `"ESMC\\jordan.grey"` to detect that user on any reported workstation. For disappearance transitions, compare the current value and previous value with their respective recursive operators. Failed source polls do not update current/previous values.

## Custom stored variables

jRULE maintains a reserved `variables` object in the same global pool. Variables persist in SQLite and are available to rules as:

```text
current.variables.some_name
previous.variables.some_name
meta.variables.some_name.last_changed
```

They can be created and edited in the Variables tab. Rules can write them with the built-in action:

```json
{
  "method": "variables.set",
  "arguments": {
    "name": "some_name",
    "value": true
  }
}
```

Writing a variable advances only that variable's own current/last pair.

## Configure an action

- Name: `send_alert`
- Endpoint: `/v1/clinalert/send` (verify the actual deployed POST endpoint)
- Body: `{ "recipient": "{{ current.presence.user }}", "message": "Hello" }`

Create a rule with a condition JSON document and action entry `{"method":"send_alert","arguments":{}}`. Templates can use `{{ args.name }}` for rule arguments. An action must be individually enabled **and** `JRULE_ENABLE_ACTIONS=1`; jRULE does not automatically retry failed POSTs.

## Validation

```bash
/opt/jrule/venv/bin/python -m unittest discover -s tests -v
curl http://127.0.0.1:8096/api/version
```

The unit tests reside in the source directory, not necessarily under `/opt/jrule`, so run tests from the extracted package's top-level directory with `/opt/jrule/venv/bin/python`.

## GitHub publishing

Repository: `https://github.com/ProfessorOfJankology/jRULE`. After verifying the installed build, commit the **source directory**, not `.env` secrets, SQLite databases, or the virtual environment. A `.gitignore` is provided.
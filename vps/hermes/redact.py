#!/usr/bin/env python3
"""Redact secrets from Hermes config snapshots before they land in git.

Used by snapshot.sh — not meant to be run standalone against files you
haven't already reviewed. Two passes:

1. Structured key redaction (YAML `key: value` and JSON `"key": "value"`
   pairs) for a fixed list of credential-shaped keys.
2. Inline pattern redaction for token formats that show up pasted into
   free text (cron job prompts, webhook descriptions) rather than in a
   clean key: value field — Bearer headers and known token prefixes.

Redaction convention: every secret value is replaced with
`<secret:NAME>` where NAME is the field/context name uppercased. This is
NOT exhaustive secret detection — it targets the patterns actually seen
in Hermes config on this VPS. Re-run the final `grep` step in
snapshot.sh and eyeball the diff before committing.
"""
import json
import re
import sys

# Keys whose values are always credentials, redacted by field name.
SECRET_KEYS = {
    "token", "secret", "password", "api_key", "session_key",
    "client_secret", "access_token", "refresh_token", "auth_token",
    "webhook_secret",
}

# Inline patterns for tokens pasted into prose (cron job prompts etc).
INLINE_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}"), "Bearer <secret:BEARER_TOKEN>"),
    (re.compile(r"(?<![A-Za-z0-9])ol_api_[A-Za-z0-9]{10,}"), "<secret:OUTLINE_API_TOKEN>"),
    (re.compile(r"(?<![A-Za-z0-9])crw_[A-Za-z0-9]{10,}"), "<secret:WINDSHIFT_API_TOKEN>"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{16,}"), "<secret:SK_TOKEN>"),
    (re.compile(r"(?<![A-Za-z])ll-[A-Za-z0-9_\-]{16,}"), "<secret:LIGHTLLM_API_KEY>"),
    (re.compile(r"(?<![A-Za-z0-9:])[0-9]{6,12}:[A-Za-z0-9_\-]{20,}"), "<secret:TELEGRAM_BOT_TOKEN>"),
]

EMPTY_VALUES = {"", "''", '""', "null", "~"}


def redact_inline(text: str) -> str:
    for pattern, placeholder in INLINE_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def redact_yaml(text: str) -> str:
    out_lines = []
    key_line_re = re.compile(r"^(\s*)([A-Za-z0-9_]+):\s*(.*)$")
    for line in text.splitlines():
        m = key_line_re.match(line)
        if m:
            indent, key, value = m.groups()
            stripped = value.strip().strip("'\"")
            if key.lower() in SECRET_KEYS and stripped not in EMPTY_VALUES and not stripped.startswith("<secret:"):
                out_lines.append(f"{indent}{key}: <secret:{key.upper()}>")
                continue
        out_lines.append(redact_inline(line))
    return "\n".join(out_lines) + "\n"


def redact_json_value(value, key_hint=""):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k.lower() in SECRET_KEYS and isinstance(v, str) and v.strip() and not v.startswith("<secret:"):
                result[k] = f"<secret:{key_hint.upper() or k.upper()}>"
            else:
                result[k] = redact_json_value(v, key_hint=k)
        return result
    if isinstance(value, list):
        return [redact_json_value(v, key_hint=key_hint) for v in value]
    if isinstance(value, str):
        return redact_inline(value)
    return value


def redact_json_webhooks(text: str) -> str:
    data = json.loads(text)
    out = {}
    for name, entry in data.items():
        redacted = dict(entry)
        if isinstance(redacted.get("secret"), str) and redacted["secret"].strip():
            redacted["secret"] = f"<secret:webhook_{name}>"
        out[name] = redact_json_value(redacted, key_hint=name)
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def redact_json_jobs(text: str) -> str:
    data = json.loads(text)
    data = redact_json_value(data)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main():
    if len(sys.argv) != 3:
        print("usage: redact.py <mode: yaml|json-webhooks|json-jobs> <path>", file=sys.stderr)
        sys.exit(1)
    mode, path = sys.argv[1], sys.argv[2]
    with open(path) as f:
        text = f.read()
    if mode == "yaml":
        sys.stdout.write(redact_yaml(text))
    elif mode == "json-webhooks":
        sys.stdout.write(redact_json_webhooks(text))
    elif mode == "json-jobs":
        sys.stdout.write(redact_json_jobs(text))
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

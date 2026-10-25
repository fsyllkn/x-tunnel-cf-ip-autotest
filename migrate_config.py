#!/usr/bin/env python3
"""One-time, idempotent migration of the two legacy .env files into x-tunnel.yaml.

Preserves original node row spelling/comments required by the CF IP writer.
Does not print secrets. Legacy files are moved to a private backup directory
only after the unified configuration has been durably committed.
"""
import json
import os
import re
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path

import yaml
from project_settings import CONFIG_FILE, BASE_DIR, config_lock, write_config_atomic

LEGACY_PROJECT = BASE_DIR / "project.env"
LEGACY_BOT = BASE_DIR / "speedtest-bot.env"
PROJECT_MAPPING = {
    "X_TUNNEL_PROVIDER_PATH": ("settings", "provider_path"),
    "X_TUNNEL_PROCESS_UID": ("settings", "process_uid"),
    "X_TUNNEL_PROCESS_GID": ("settings", "process_gid"),
    "X_TUNNEL_TG_PROXY": ("settings", "telegram_proxy"),
    "X_TUNNEL_LOCAL_PROXY": ("settings", "local_proxy"),
    "X_TUNNEL_TARGET_MBPS": ("settings", "target_mbps"),
}
BOT_MAPPING = {
    "TG_TOKEN": ("telegram", "token"),
    "TG_CHATID": ("telegram", "chat_id"),
}
# These defaults are only a fallback if the example file was not shipped.
# Comparing with shipped template defaults prevents fresh example values from
# hiding real values in an existing project.env during an upgrade.
FALLBACK_SETTINGS = {
    "provider_path": "runtime/x-tunnel-provider.json",
    "process_uid": 29002,
    "process_gid": "auto",
    "local_proxy": "http://127.0.0.1:7890",
    "telegram_proxy": "",
    "target_mbps": 300,
}
FALLBACK_TELEGRAM = {"token": "", "chat_id": ""}


def template_defaults():
    template = BASE_DIR / "x-tunnel.example.yaml"
    if not template.is_file():
        return FALLBACK_SETTINGS.copy(), FALLBACK_TELEGRAM.copy()
    document = load_yaml(template.read_text(encoding="utf-8"))
    settings = FALLBACK_SETTINGS.copy()
    settings.update(document.get("settings") or {})
    telegram = FALLBACK_TELEGRAM.copy()
    telegram.update(document.get("telegram") or {})
    return settings, telegram


def replace_scalar(source, section, key, value):
    """Change only a known template-default scalar, retaining node rows verbatim."""
    start = re.search(r"(?m)^" + re.escape(section) + r":\s*(?:#.*)?$", source)
    if start is None:
        raise ValueError("%s must use a block mapping" % section)
    tail = source[start.end():]
    end_match = re.search(r"(?m)^[^\s#][^:\n]*:", tail)
    block_end = start.end() + end_match.start() if end_match else len(source)
    block = source[start.end():block_end]
    expr = re.compile(r"(?m)^(  " + re.escape(key) + r"\s*:)\s*[^\n\r]*(\r?)$")
    if len(expr.findall(block)) != 1:
        raise ValueError("expected one scalar for %s.%s" % (section, key))
    new_block = expr.sub(lambda m: m.group(1) + " " +
                         json.dumps(value, ensure_ascii=False) + m.group(2),
                         block, count=1)
    return source[:start.end()] + new_block + source[block_end:]


def parse_env(path):
    if path.is_symlink():
        raise ValueError("%s must not be a symlink" % path.name)
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("%s must be a regular file" % path.name)
    result = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        try:
            parsed = shlex.split(value, comments=True)
        except ValueError as error:
            raise ValueError("%s line %d contains malformed shell quoting" % (path.name, index)) from error
        result[key] = parsed[0] if parsed else ""
    return result


def load_yaml(source):
    document = yaml.safe_load(source) or {}
    if not isinstance(document, dict):
        raise ValueError("x-tunnel.yaml must be a mapping")
    for section in ("defaults", "settings", "telegram"):
        if section in document and not isinstance(document[section], dict):
            raise ValueError("%s must be a block mapping" % section)
    return document


def write_scalar(source, section, key, value):
    """Insert one key into a top-level block mapping without touching nodes."""
    lines = source.splitlines(keepends=True)
    expr = re.compile(r"^" + re.escape(section) + r":(?:\s*#.*)?\s*$")
    starts = [i for i, line in enumerate(lines) if expr.match(line.strip("\r\n"))]
    if not starts:
        if re.search(r"(?m)^" + re.escape(section) + r":", source):
            raise ValueError("%s: unsupported inline YAML section; migrate by hand" % section)
        return source.rstrip("\r\n") + "\n\n" + section + ":\n  " + key + ": " + json.dumps(value, ensure_ascii=False) + "\n"
    if len(starts) != 1:
        raise ValueError("%s: duplicate YAML block" % section)
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^[^\s#][^:]*:", lines[i])), len(lines))
    if any(re.match(r"^  " + re.escape(key) + r"\s*:", lines[i])
           for i in range(start + 1, end)):
        raise ValueError("conflicting %s.%s already exists" % (section, key))
    lines.insert(end, "  " + key + ": " + json.dumps(value, ensure_ascii=False) + "\n")
    return "".join(lines)


def change_yaml_listener(source, hosts):
    """Migrate explicit legacy listener only if YAML was still localhost-only."""
    document = load_yaml(source)
    defaults = document.get("defaults") or {}
    actual = defaults.get("listen_hosts", ["127.0.0.1"])
    if actual != ["127.0.0.1"]:
        return source, False
    if hosts == ["127.0.0.1"]:
        return source, False
    lines = source.splitlines(keepends=True)
    header = next((i for i, line in enumerate(lines)
                   if re.match(r"^defaults:\s*(?:#.*)?$", line.strip("\r\n"))), None)
    if header is None:
        raise ValueError("defaults must use a YAML block mapping")
    end = next((i for i in range(header + 1, len(lines))
                if re.match(r"^[^\s#][^:]*:", lines[i])), len(lines))
    start = next((i for i in range(header + 1, end)
                  if re.match(r"^  listen_hosts\s*:", lines[i])), None)
    replacement = ["  listen_hosts:\n"] + [
        "    - %s\n" % json.dumps(host, ensure_ascii=False) for host in hosts
    ]
    if start is None:
        lines[end:end] = replacement
    else:
        block_end = next((i for i in range(start + 1, end)
                          if re.match(r"^  [A-Za-z_][\w-]*\s*:", lines[i])), end)
        # Remove only the list elements, keeping unrelated YAML sections intact.
        lines[start:block_end] = replacement
    return "".join(lines), True


def migrate():
    if CONFIG_FILE.is_symlink():
        raise ValueError("x-tunnel.yaml cannot be a symlink")
    if not CONFIG_FILE.is_file():
        raise FileNotFoundError("create x-tunnel.yaml from x-tunnel.example.yaml before migration")
    with config_lock():
        project_present = LEGACY_PROJECT.exists() or LEGACY_PROJECT.is_symlink()
        bot_present = LEGACY_BOT.exists() or LEGACY_BOT.is_symlink()
        project = parse_env(LEGACY_PROJECT) if project_present else {}
        bot = parse_env(LEGACY_BOT) if bot_present else {}
        original = CONFIG_FILE.read_text(encoding="utf-8")
        source = original
        document = load_yaml(source)
        defaults, bot_defaults = template_defaults()
        changed_keys = []
        # A new YAML created from the example contains settings defaults,
        # not the user's migrated settings. Prefer old env over TEMPLATE
        # values only; preserve any customised YAML setting, even when
        # different from the legacy env. Never print secret values.
        for legacy_key, (section, field) in PROJECT_MAPPING.items():
            value = project.get(legacy_key, "")
            if not value:
                continue
            if field in ("process_uid", "target_mbps"):
                if not value.isdecimal():
                    raise ValueError("invalid numeric legacy setting %s" % legacy_key)
                value = int(value)
            current_section = document.get(section) or {}
            if field in current_section:
                old = current_section[field]
                if old == value:
                    continue
                if old != defaults.get(field):
                    continue  # Genuine customized YAML wins.
                source = replace_scalar(source, section, field, value)
            else:
                source = write_scalar(source, section, field, value)
            document = load_yaml(source)
            changed_keys.append(section + "." + field)

        for legacy_key, (_, field) in BOT_MAPPING.items():
            value = bot.get(legacy_key, "")
            if not value or "替换为" in value:
                continue
            current_section = document.get("telegram") or {}
            existing = str(current_section.get(field) or "")
            if existing and "替换为" not in existing:
                continue  # Existing custom YAML credential wins.
            if existing:
                raise ValueError("unified telegram.%s has placeholder; migrate manually" % field)
            if field in current_section:
                source = replace_empty_telegram_scalar(source, field, value)
            else:
                source = write_scalar(source, "telegram", field, value)
            document = load_yaml(source)
            changed_keys.append("telegram." + field)

        legacy_hosts = project.get("X_TUNNEL_LISTEN_HOSTS", "")
        if legacy_hosts.strip():
            import ipaddress
            hosts = [str(ipaddress.ip_address(h.strip()))
                     for h in legacy_hosts.split(",") if h.strip()]
            if not hosts:
                raise ValueError("X_TUNNEL_LISTEN_HOSTS contains no IP")
            source, listen_changed = change_yaml_listener(source, hosts)
            if listen_changed:
                changed_keys.append("defaults.listen_hosts")
            document = load_yaml(source)

        # Always finish normalising older YAML, even when no .env files
        # remain. The only active file must have a complete settings/telegram
        # layout; a successful previous partial migration must be repairable.
        for field, value in defaults.items():
            if field not in (document.get("settings") or {}):
                source = write_scalar(source, "settings", field, value)
                document = load_yaml(source)
                changed_keys.append("settings." + field)
        for field, value in bot_defaults.items():
            if field not in (document.get("telegram") or {}):
                source = write_scalar(source, "telegram", field, value)
                document = load_yaml(source)
                changed_keys.append("telegram." + field)

        merged = load_yaml(source)
        telegram = merged.get("telegram") or {}
        chat = str(telegram.get("chat_id") or "")
        if chat and not chat.isdecimal() and "替换为" not in chat:
            raise ValueError("telegram.chat_id must be numeric")
        if not project_present and not bot_present and source == original:
            os.chmod(CONFIG_FILE, 0o600)
            print("One-file configuration verified: x-tunnel.yaml.")
            return False

        backup_dir = BASE_DIR / "cfip-data" / "config-backups" / (
            "%s-%s-%d" % (
                "legacy-env" if project_present or bot_present else "unified-yaml",
                time.strftime("%Y%m%d-%H%M%S"), os.getpid()
            )
        )
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(backup_dir, 0o700)
        backup_yaml = backup_dir / "x-tunnel.yaml.before-migration"
        shutil.copyfile(CONFIG_FILE, backup_yaml)
        os.chmod(backup_yaml, 0o600)
        if source != original:
            write_config_atomic(source)
        else:
            os.chmod(CONFIG_FILE, 0o600)
        # Originals are archived only AFTER the full merged YAML is durable.
        # Failed YAML validation/copy must never delete the source credentials.
        for path in (LEGACY_PROJECT, LEGACY_BOT):
            if path.exists():
                target = backup_dir / path.name
                shutil.copyfile(path, target)
                os.chmod(target, 0o600)
        for path in (LEGACY_PROJECT, LEGACY_BOT):
            if path.exists():
                path.unlink()
        print("Unified x-tunnel.yaml: updated %d field(s); private backups in %s" %
              (len(changed_keys), backup_dir))
        if legacy_hosts.strip() and "defaults.listen_hosts" not in changed_keys:
            print("Note: custom YAML defaults.listen_hosts retained over legacy env.")
        return True


def replace_empty_telegram_scalar(source, field, value):
    pattern = re.compile(r"(?m)^  " + re.escape(field) + r":\s*(?:\"\"|''|null|~)?\s*$")
    if not pattern.search(source):
        raise ValueError("telegram.%s is not an empty block scalar" % field)
    return pattern.sub("  " + field + ": " + json.dumps(value, ensure_ascii=False),
                       source, count=1)


if __name__ == "__main__":
    try:
        migrate()
    except (OSError, ValueError, yaml.YAMLError) as error:
        # Messages contain field names/paths, not secret values.
        print("Configuration migration refused: %s" % error, file=sys.stderr)
        sys.exit(1)

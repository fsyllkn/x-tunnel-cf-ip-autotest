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
    "X_TUNNEL_TG_PROXY": ("settings", "telegram_proxy"),
    "X_TUNNEL_LOCAL_PROXY": ("settings", "local_proxy"),
    "X_TUNNEL_TARGET_MBPS": ("settings", "target_mbps"),
}
BOT_MAPPING = {
    "TG_TOKEN": ("telegram", "token"),
    "TG_CHATID": ("telegram", "chat_id"),
}


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
        if not project_present and not bot_present:
            os.chmod(CONFIG_FILE, 0o600)
            print("One-file configuration active: x-tunnel.yaml (no legacy .env files).")
            return False
        project = parse_env(LEGACY_PROJECT)
        bot = parse_env(LEGACY_BOT)
        original = CONFIG_FILE.read_text(encoding="utf-8")
        source = original
        document = load_yaml(source)
        changed_keys = []
        for legacy_key, (section, field) in PROJECT_MAPPING.items():
            value = project.get(legacy_key, "")
            if not value:
                continue
            if section == "settings" and field in (document.get(section) or {}):
                continue  # Unified YAML wins over deprecated .env keys.
            if field in ("process_uid", "target_mbps"):
                if not value.isdecimal():
                    raise ValueError("invalid numeric legacy setting %s" % legacy_key)
                value = int(value)
            source = write_scalar(source, section, field, value)
            document = load_yaml(source)
            changed_keys.append(section + "." + field)
        for legacy_key, (_, field) in BOT_MAPPING.items():
            value = bot.get(legacy_key, "")
            if not value or "替换为" in value:
                continue
            existing = str((document.get("telegram") or {}).get(field) or "")
            if existing and "替换为" not in existing:
                continue
            if existing:
                # Pre-existing placeholder in unified file should not silently
                # block safe credentials migration. Require manual review.
                raise ValueError("unified telegram.%s contains placeholder; migrate manually" % field)
            if "telegram" in document and field in document["telegram"]:
                # Existing explicit empty value belongs to the new file; import
                # legacy real credentials instead of silently losing the Bot.
                source = replace_empty_telegram_scalar(source, field, value)
            else:
                source = write_scalar(source, "telegram", field, value)
            document = load_yaml(source)
            changed_keys.append("telegram." + field)
        legacy_hosts = project.get("X_TUNNEL_LISTEN_HOSTS", "")
        if legacy_hosts.strip():
            hosts = [h.strip() for h in legacy_hosts.split(",") if h.strip()]
            # Same normalized numeric addresses accepted by supervisor.
            import ipaddress
            hosts = [str(ipaddress.ip_address(h)) for h in hosts]
            if not hosts:
                raise ValueError("X_TUNNEL_LISTEN_HOSTS contains no IP")
            source, listen_changed = change_yaml_listener(source, hosts)
            if listen_changed:
                changed_keys.append("defaults.listen_hosts")
        merged = load_yaml(source)
        if merged.get("telegram"):
            token = str(merged["telegram"].get("token") or "")
            chat = str(merged["telegram"].get("chat_id") or "")
            if chat and not chat.isdecimal() and "替换为" not in chat:
                raise ValueError("telegram.chat_id must be numeric")
        # Backup existing YAML and env files, before changing anything.
        backup_dir = BASE_DIR / "cfip-data" / "config-backups" / (
            "legacy-env-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
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
        # Copy first: if interrupted, next run can safely retry migration.
        for path in (LEGACY_PROJECT, LEGACY_BOT):
            if path.exists():
                target = backup_dir / path.name
                shutil.copyfile(path, target)
                os.chmod(target, 0o600)
        for path in (LEGACY_PROJECT, LEGACY_BOT):
            if path.exists():
                path.unlink()  # Canonical YAML is now the only active configuration.
        print("Migrated %d field(s) into x-tunnel.yaml; archived legacy files in %s" %
              (len(changed_keys), backup_dir))
        if legacy_hosts.strip() and not ("defaults.listen_hosts" in changed_keys):
            print("Note: kept existing YAML defaults.listen_hosts; old env listener differed.")
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

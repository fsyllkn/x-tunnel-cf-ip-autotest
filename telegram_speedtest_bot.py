#!/usr/bin/env python3
import argparse
import fcntl
import ipaddress
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from project_settings import (
    MAX_TARGET_MBPS,
    MIN_TARGET_MBPS,
    get_target_mbps,
    load_project_env,
    set_target_mbps,
    telegram_proxy_chain,
)


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("X_TUNNEL_BOT_CONFIG", str(BASE_DIR / "speedtest-bot.env")))
BOT_LOCK_FILE = Path("/run/lock/x-tunnel-speedtest-bot.lock")
SPEEDTEST_SERVICE = "x-tunnel-speedtest.service"
SINGLE_SPEEDTEST_TEMPLATE = "x-tunnel-speedtest-single@.service"
BOT_USERNAME = "cfipspdtest_bot"


def read_config(require_chat_id=True):
    values = {}
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        try:
            parsed = shlex.split(match.group(2), comments=True)
        except ValueError:
            continue
        values[match.group(1)] = parsed[0] if parsed else ""
    token = values.get("TG_TOKEN", "")
    chat_id = values.get("TG_CHATID", "")
    if not token:
        raise ValueError("TG_TOKEN is missing from %s" % CONFIG_FILE)
    if require_chat_id and not chat_id:
        raise ValueError("TG_CHATID is missing from %s" % CONFIG_FILE)
    if chat_id and not chat_id.isdecimal():
        raise ValueError("TG_CHATID must be the numeric ID of your private chat")
    if stat.S_IMODE(CONFIG_FILE.stat().st_mode) & 0o077:
        raise PermissionError("%s must have mode 0600 or stricter" % CONFIG_FILE)
    return token, chat_id


def api_call(token, method, payload=None):
    encoded = None
    headers = {}
    if payload is not None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    endpoint = "https://api.telegram.org/bot%s/%s" % (token, method)
    request = urllib.request.Request(endpoint, data=encoded, headers=headers)
    last_error = None
    for proxy in telegram_proxy_chain():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        try:
            with opener.open(request, timeout=40) as response:
                result = json.loads(response.read(1024 * 1024).decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError("Telegram API rejected %s request" % method)
            return result.get("result")
        except (OSError, TimeoutError, ValueError, urllib.error.URLError) as error:
            last_error = error
    raise RuntimeError("Telegram API %s failed through configured proxies" % method) from last_error


def pending_private_chats(token):
    updates = api_call(token, "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}) or []
    chats = {}
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        if chat.get("type") != "private":
            continue
        chats[str(chat.get("id", ""))] = message.get("from", {}).get("username", "")
    return chats


def show_chat_ids():
    token, _ = read_config(require_chat_id=False)
    chats = pending_private_chats(token)
    if not chats:
        print("No pending private chat found. Send /start to @%s, then retry." % BOT_USERNAME)
        return 1
    for chat_id, username in sorted(chats.items()):
        print("chat_id=%s username=@%s" % (chat_id, username or "(not set)"))
    return 0


def send_message(token, chat_id, text):
    api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def launch_speedtest():
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", SPEEDTEST_SERVICE],
        check=False,
    ).returncode == 0:
        return False, "测速已经在运行，请稍后查看结果。"
    result = subprocess.run(
        ["systemctl", "start", "--no-block", SPEEDTEST_SERVICE],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return False, "无法启动测速服务；请确认已安装 x-tunnel-speedtest.service。"
    return True, "已启动 CF IP 测速。测速候选 IP 会临时直连绕过 ShellCrash，结束后发送结果。"


def launch_single_speedtest(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False, "IP 地址格式无效。用法：/speedtest <IP>，例如 /speedtest 104.17.76.76"
    ip = str(address)
    escaped = subprocess.run(
        ["systemd-escape", "--template=" + SINGLE_SPEEDTEST_TEMPLATE, ip],
        capture_output=True,
        text=True,
        check=False,
    )
    if escaped.returncode:
        return False, "无法构造单 IP 测速服务名称，请检查 systemd-escape。"
    unit = escaped.stdout.strip()
    if subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0:
        return False, "该 IP 的单独测速已经在运行。"
    result = subprocess.run(
        ["systemctl", "start", "--no-block", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return False, "无法启动单 IP 测速；请确认已安装 %s。" % SINGLE_SPEEDTEST_TEMPLATE
    return True, "已启动 IP %s 的单独测速：30 秒，取第 5–30 秒均速；不运行自动选取，也不会改写节点配置。完成后会发送结果。" % ip


def restart_xtunnel():
    try:
        result = subprocess.run(
            ["systemctl", "restart", "x-tunnel.service"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "重启 x-tunnel 服务失败或超时，请检查设备上的 systemd 日志。"
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        return "重启 x-tunnel 服务失败。%s" % detail[:500]
    return "x-tunnel 服务已重启。"


def handle_update(token, allowed_chat_id, update):
    message = update.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private" or str(chat.get("id", "")) != allowed_chat_id:
        return
    text = message.get("text", "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
    if command == "/start" or command == "/help":
        send_message(
            token,
            allowed_chat_id,
            "测速 bot 已就绪。\n/speedtest 开始完整测速\n/speedtest <IP> 单独测速该 IP（30 秒，不改配置）\n/topspeed 查看或设置目标 Mbps\n/restartxtunnel 重启 X-Tunnel 服务。",
        )
    elif command == "/speedtest":
        arguments = text.split(maxsplit=1)
        if len(arguments) == 1:
            _, reply = launch_speedtest()
        else:
            _, reply = launch_single_speedtest(arguments[1])
        send_message(token, allowed_chat_id, reply)
    elif command in ("/restart", "/restartxtunnel"):
        send_message(token, allowed_chat_id, restart_xtunnel())
    elif command == "/topspeed":
        arguments = text.split(maxsplit=1)
        if len(arguments) == 1:
            send_message(token, allowed_chat_id, "当前测速目标：%d Mbps；达标 IP 数量仍为 2。" % get_target_mbps())
            return
        try:
            value = arguments[1].strip()
            if not value.isdecimal():
                raise ValueError("请使用整数 Mbps，例如 /topspeed 300。")
            target = int(value)
            if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
                raise ValueError("目标速度范围为 %d–%d Mbps。" % (MIN_TARGET_MBPS, MAX_TARGET_MBPS))
            set_target_mbps(target)
            reply = "测速目标已设为 %d Mbps；达标 IP 数量仍为 2。" % target
        except (OSError, ValueError) as error:
            reply = str(error)
        send_message(token, allowed_chat_id, reply)


def run_bot():
    load_project_env()
    token, allowed_chat_id = read_config()
    api_call(token, "setMyCommands", {
        "commands": [
            {"command": "start", "description": "检查测速机器人状态"},
            {"command": "help", "description": "显示可用命令"},
            {"command": "speedtest", "description": "自动测速，或 /speedtest <IP> 单测"},
            {"command": "topspeed", "description": "查看或设置目标 Mbps"},
            {"command": "restart", "description": "重启 x-tunnel 服务"},
            {"command": "restartxtunnel", "description": "重启 X-Tunnel 服务"},
        ],
    })
    with BOT_LOCK_FILE.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another dedicated speed-test bot process is running") from error

        offset = 0
        while True:
            try:
                updates = api_call(token, "getUpdates", {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                }) or []
                for update in updates:
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    try:
                        handle_update(token, allowed_chat_id, update)
                    except Exception:
                        print("Failed to process a speed-test bot update", file=sys.stderr, flush=True)
            except Exception:
                print("Speed-test bot polling failed; retrying shortly", file=sys.stderr, flush=True)
                time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Dedicated Telegram bot adapter for X-Tunnel speed tests")
    parser.add_argument(
        "--show-chat-ids",
        action="store_true",
        help="show pending private chat IDs for local setup; does not print message text",
    )
    args = parser.parse_args()
    try:
        if args.show_chat_ids:
            return show_chat_ids()
        run_bot()
    except (OSError, ValueError, RuntimeError) as error:
        print("telegram_speedtest_bot: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

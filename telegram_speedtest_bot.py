#!/usr/bin/env python3
import argparse
import fcntl
import ipaddress
import json
import os
import re
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
    read_telegram_credentials,
)
from platform_services import is_openwrt, is_systemd, launch_speedtest as launch_openwrt_speedtest, restart_xtunnel as restart_openwrt_xtunnel, speedtest_running


BASE_DIR = Path(__file__).resolve().parent
BOT_LOCK_FILE = Path("/run/lock/x-tunnel-speedtest-bot.lock")
SPEEDTEST_SERVICE = "x-tunnel-speedtest.service"
SINGLE_SPEEDTEST_TEMPLATE = "x-tunnel-speedtest-single@.service"
BOT_USERNAME = "cfipspdtest_bot"


def read_config(require_chat_id=True):
    token, chat_id = read_telegram_credentials(require_chat_id=require_chat_id)
    if not token:
        raise ValueError("telegram.token is missing from x-tunnel.yaml")
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
    last_reason = "no configured proxy"
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
        except urllib.error.HTTPError as error:
            last_error = error
            last_reason = "HTTP %d" % error.code
            if error.code == 409:
                last_reason += " (another polling process or webhook may be consuming updates)"
            elif error.code == 401:
                last_reason += " (check TG_TOKEN)"
            elif error.code == 403:
                last_reason += " (Bot may be blocked by the recipient)"
        except (OSError, TimeoutError, ValueError, urllib.error.URLError) as error:
            last_error = error
            last_reason = type(error).__name__
    raise RuntimeError(
        "Telegram API %s failed: %s; check proxy reachability and Bot credentials"
        % (method, last_reason)
    ) from last_error


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
    if is_openwrt() and not is_systemd():
        if speedtest_running():
            return False, "测速已经在运行，请稍后查看结果。"
        try:
            process = launch_openwrt_speedtest()
            time.sleep(0.5)
            if process.poll() is not None:
                return False, "测速进程启动后立即退出（退出码 %d），请检查 cfip-data/openwrt-speedtest.log。" % process.returncode
        except OSError:
            return False, "无法启动测速进程，请检查 cfip-data/openwrt-speedtest.log。"
        return True, "已启动 CF IP 测速（PID %d）。测速进度和结果将通过 Bot 通知。" % process.pid
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
    if is_openwrt() and not is_systemd():
        if speedtest_running():
            return False, "已有测速任务正在运行，请稍后再试。"
        try:
            process = launch_openwrt_speedtest(ip)
            time.sleep(0.5)
            if process.poll() is not None:
                return False, "单 IP 测速进程启动后立即退出（退出码 %d），请检查 cfip-data/openwrt-speedtest.log。" % process.returncode
        except OSError:
            return False, "无法启动单 IP 测速，请检查 cfip-data/openwrt-speedtest.log。"
        return True, "已启动 IP %s 的单独测速（PID %d）：完整采样 30 秒；均速达到 100 Mbps 且波动稳定后才写入匹配地区节点。完成后会发送测速与写入结果。" % (ip, process.pid)
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
    return True, "已启动 IP %s 的单独测速：完整采样 30 秒；均速达到 100 Mbps 且波动稳定后才写入匹配地区节点。完成后会发送测速与写入结果。" % ip


def restart_xtunnel():
    if is_openwrt() and not is_systemd():
        try:
            result = restart_openwrt_xtunnel()
        except (OSError, subprocess.TimeoutExpired):
            return "重启 x-tunnel 服务失败或超时，请检查 /etc/init.d/x-tunnel。"
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            return "重启 x-tunnel 服务失败。%s" % detail[:500]
        return "x-tunnel 服务已重启。"
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
    if chat.get("type") != "private":
        return
    if str(chat.get("id", "")) != allowed_chat_id:
        if str(message.get("text", "")).startswith("/"):
            print("Bot ignored command from a different private chat; check TG_CHATID.", file=sys.stderr, flush=True)
        return
    text = message.get("text", "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
    if command in ("/start", "/help", "/speedtest", "/topspeed", "/restart", "/restartxtunnel"):
        print("Bot received %s from authorized private chat" % command, flush=True)
    if command == "/start" or command == "/help":
        send_message(
            token,
            allowed_chat_id,
            "测速 bot 已就绪。\n/speedtest 开始完整测速\n/speedtest <IP> 单独测速该 IP（完整 30 秒；均速达到 100 Mbps 且稳定后写入匹配地区节点）\n/topspeed 查看或设置目标 Mbps\n/restartxtunnel 重启 X-Tunnel 服务。",
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
            send_message(token, allowed_chat_id, "当前测速目标：%d Mbps；默认按有效节点数选取，最多 3 个 IP。" % get_target_mbps())
            return
        try:
            value = arguments[1].strip()
            if not value.isdecimal():
                raise ValueError("请使用整数 Mbps，例如 /topspeed 300。")
            target = int(value)
            if not MIN_TARGET_MBPS <= target <= MAX_TARGET_MBPS:
                raise ValueError("目标速度范围为 %d–%d Mbps。" % (MIN_TARGET_MBPS, MAX_TARGET_MBPS))
            set_target_mbps(target)
            reply = "测速目标已设为 %d Mbps；默认按有效节点数选取，最多 3 个 IP。" % target
        except (OSError, ValueError) as error:
            reply = str(error)
        send_message(token, allowed_chat_id, reply)
    elif command.startswith("/"):
        send_message(token, allowed_chat_id, "未知命令，请发送 /help 查看可用命令。")


def diagnose_bot(send_test_message=False):
    load_project_env()
    token, chat_id = read_config()
    print("Bot diagnostics: checking Telegram API through configured proxy (no tokens will be printed)", flush=True)
    me = api_call(token, "getMe")
    if not isinstance(me, dict) or not me.get("id"):
        raise RuntimeError("Telegram getMe returned an invalid response")
    print("Telegram getMe: OK; username=@%s" % me.get("username", "(not set)"), flush=True)
    webhook = api_call(token, "getWebhookInfo")
    if isinstance(webhook, dict) and webhook.get("url"):
        print("Webhook: CONFIGURED; long polling via getUpdates will not work until webhook is removed", flush=True)
        return 2
    print("Webhook: not configured (getUpdates can be used)", flush=True)
    if send_test_message:
        send_message(token, chat_id, "X-Tunnel Bot 通信测试成功。现在可以发送 /speedtest 启动测速。")
        print("Test message: sent to the configured private TG_CHATID", flush=True)
    print("Note: this diagnostic does not call getUpdates, so it will not conflict with a running Bot.", flush=True)
    return 0


def run_bot():
    load_project_env()
    token, allowed_chat_id = read_config()
    try:
        api_call(token, "setMyCommands", {
        "commands": [
            {"command": "start", "description": "检查测速机器人状态"},
            {"command": "help", "description": "显示可用命令"},
            {"command": "speedtest", "description": "自动测速，或单测 IP 并按地区写入达标结果"},
            {"command": "topspeed", "description": "查看或设置目标 Mbps"},
            {"command": "restart", "description": "重启 x-tunnel 服务"},
            {"command": "restartxtunnel", "description": "重启 X-Tunnel 服务"},
        ],
        })
    except RuntimeError as error:
        # Optional command registration must not prevent polling and handling /speedtest.
        print("Warning: Telegram setMyCommands failed: %s" % error, file=sys.stderr, flush=True)
    print("Bot starting Telegram long polling (authorized private chat only)", flush=True)
    with BOT_LOCK_FILE.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another dedicated speed-test bot process is running") from error

        offset = 0
        consecutive_failures = 0
        while True:
            try:
                updates = api_call(token, "getUpdates", {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                }) or []
                if consecutive_failures:
                    print("Telegram polling recovered", flush=True)
                    consecutive_failures = 0
                for update in updates:
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    try:
                        handle_update(token, allowed_chat_id, update)
                    except Exception as error:
                        # Avoid printing raw URLs or Telegram token in error details.
                        print("Bot command handling failed (%s): %s" %
                              (type(error).__name__,
                               str(error) if isinstance(error, RuntimeError) else "check dependencies/permissions"),
                              file=sys.stderr, flush=True)
            except Exception as error:
                consecutive_failures += 1
                if consecutive_failures == 1 or consecutive_failures % 12 == 0:
                    print("Telegram polling failed (%s): %s; retrying shortly" %
                          (type(error).__name__,
                           str(error) if isinstance(error, RuntimeError) else "check proxy/network"),
                          file=sys.stderr, flush=True)
                time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Dedicated Telegram bot adapter for X-Tunnel speed tests")
    parser.add_argument(
        "--show-chat-ids",
        action="store_true",
        help="show pending private chat IDs for local setup; does not print message text",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="check Bot credentials, proxy and Telegram webhook without consuming updates",
    )
    parser.add_argument(
        "--send-test-message", action="store_true",
        help="with --diagnose, send a test message to configured private TG_CHATID",
    )
    args = parser.parse_args()
    if args.send_test_message and not args.diagnose:
        parser.error("--send-test-message requires --diagnose")
    try:
        if args.diagnose:
            return diagnose_bot(args.send_test_message)
        if args.show_chat_ids:
            return show_chat_ids()
        run_bot()
    except (OSError, ValueError, RuntimeError) as error:
        print("telegram_speedtest_bot: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

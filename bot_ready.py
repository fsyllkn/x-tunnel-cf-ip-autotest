#!/usr/bin/env python3
"""Pre-start Bot credential check; never log secrets or call Telegram."""
import sys
from project_settings import read_telegram_credentials

def main():
    try:
        token, chat_id = read_telegram_credentials(require_chat_id=True)
        if not token or not chat_id:
            raise ValueError("missing credentials")
    except (OSError, ValueError, PermissionError):
        print("PENDING: configure valid telegram.token and telegram.chat_id in private x-tunnel.yaml", file=sys.stderr)
        return 10
    print("READY: Telegram credentials are configured")
    return 0

if __name__ == "__main__":
    sys.exit(main())

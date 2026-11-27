"""Manual server-console bootstrap and password recovery; never accepts a password argument."""

import argparse
import getpass
import os
from pathlib import Path
import sys

from converter.security.store import AccessError, IdentityStore, validate_password


def main():
    parser = argparse.ArgumentParser(prog="ip-media-tool-admin")
    parser.add_argument("action", choices=["bootstrap", "reset-password"])
    parser.add_argument("--username")
    args = parser.parse_args()
    log_dir = os.environ.get("IP_MEDIA_TOOL_LOG_DIR")
    store = IdentityStore(os.environ.get("IP_MEDIA_TOOL_SECURITY_DIR", "/var/lib/ip-media-tool/security"),
                          audit_path=Path(log_dir) / "audit" / "admin.jsonl" if log_dir else None)
    store.initialize()
    try:
        if args.action == "bootstrap" and store.ready():
            raise AccessError("Адміністратор уже існує. Для відновлення використовуйте reset-password.")
        username = (args.username or input("Логін: ")).strip().lower()
        display_name = input("Ім’я адміністратора: ").strip() if args.action == "bootstrap" else ""
        password = getpass.getpass("Новий пароль (12–128 символів): ")
        validate_password(password)
        if password != getpass.getpass("Повторіть пароль: "):
            raise AccessError("Паролі не збігаються.")
        if args.action == "bootstrap":
            store.bootstrap(username, display_name or username, password)
            print("Адміністратора створено. Тепер можна входити через браузер.")
        else:
            store.console_reset(username, password)
            print("Пароль змінено, попередні сесії завершені.")
    except (AccessError, EOFError, KeyboardInterrupt) as error:
        print(str(error) or "Скасовано.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate server credentials in an ignored file; never print credential values."""

import argparse
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Generate local service secrets without enabling owner login.",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = root / ".private" / ("runtime/local-secrets.env" if args.local else "deployment.env")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        print(f"Existing credentials retained: {target}")
        return
    values = {
        "INTERNAL_API_TOKEN": secrets.token_urlsafe(48),
        "SNAPSHOT_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "APP_SESSION_SECRET": secrets.token_urlsafe(48),
    }
    if not args.local:
        values["APP_ACCESS_PASSWORD"] = secrets.token_urlsafe(24)
    target.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    print(f"Credentials saved to ignored file: {target}")


if __name__ == "__main__":
    main()

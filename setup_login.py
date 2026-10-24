#!/usr/bin/env python3
"""Create/update the web login without displaying or storing the raw password."""

import argparse
import getpass
import os
from pathlib import Path

from auth import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure the Voice to Transcript login")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    username = args.username.strip()
    if not username or len(username) > 128 or any(c in username for c in "\n\r=\"'"):
        parser.error("Choose a username of 1–128 characters without quotes, line breaks, or =.")
    password = getpass.getpass("New password (at least 12 characters): ")
    if not 12 <= len(password) <= 1024:
        parser.error("Password must contain 12–1024 characters.")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match.")
    path = Path(__file__).resolve().parent / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keys = {"APP_USERNAME", "APP_PASSWORD_HASH"}
    lines = [line for line in lines if line.strip().removeprefix("export ").split("=", 1)[0].strip() not in keys]
    lines.extend([f"APP_USERNAME={username}", f"APP_PASSWORD_HASH='{hash_password(password)}'"])
    # Quotes preserve the hash's literal $ characters in Docker Compose.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines) + "\n")
    path.chmod(0o600)
    print(f"Login configured for {username}. Restart the server to apply it.")


if __name__ == "__main__":
    main()

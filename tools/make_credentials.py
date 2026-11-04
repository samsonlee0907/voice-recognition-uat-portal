"""Generate fresh developer credentials; never accept a password as a CLI argument."""

import argparse
import getpass
import hashlib
import json
import os
import secrets
from pathlib import Path


def new_credentials(username):
    if not username.isascii() or not username:
        raise ValueError("Use a nonempty ASCII developer username")
    password = getpass.getpass("New portal password (12-256 characters): ")
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12-256 characters")
    if password != getpass.getpass("Confirm portal password: "):
        raise ValueError("Passwords do not match")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return {
        "portalUsername": username,
        "portalPasswordHash": f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}",
        "portalSessionSecret": secrets.token_urlsafe(48),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="developer")
    parser.add_argument("--out", type=Path, default=Path(".local") / "credentials.parameters.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.out.resolve()
    if not target.is_relative_to(root / ".local"):
        parser.error("Write credential files only inside this repository's ignored .local directory")
    values = new_credentials(args.username)
    document = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {name: {"value": value} for name, value in values.items()},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        json.dump(document, output, indent=2)
    if os.name != "nt":
        target.chmod(0o600)
    print("Wrote new credentials to the ignored .local parameter file. Restrict its filesystem access.")


if __name__ == "__main__":
    main()

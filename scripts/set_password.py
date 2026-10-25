"""Give a seeded demo account a password and activate it.

The Chunk 1 seed users (harini@, admin@, auditor@example.com) have no password and status
PENDING_RESET, so nobody can log in as them until this is run once per account.

    .venv\\Scripts\\python.exe scripts\\set_password.py admin@example.com

The password is typed at a hidden prompt; it is never taken from the command line, never printed,
and only its Argon2 hash is stored. Only accounts in status PENDING_RESET can be activated this
way (it is a bootstrap tool, not a password-reset feature).
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from app.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db.pool import OracleDatabase  # noqa: E402

_ACTIVATE = """
    UPDATE users
       SET password_hash = :password_hash, account_status = 'ACTIVE'
     WHERE email = :email AND account_status = 'PENDING_RESET'
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: set_password.py <email>")
        return 2
    email = argv[1].strip().lower()

    password = getpass.getpass(f"New password for {email} (hidden): ")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        print(f"Password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters.")
        return 2
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match.")
        return 2

    load_dotenv()
    database = OracleDatabase.from_settings(load_settings())
    try:
        with database.transaction() as session:
            cursor = session.connection.cursor()
            cursor.execute(_ACTIVATE, {"password_hash": hash_password(password), "email": email})
            updated = cursor.rowcount
    finally:
        database.close()

    if updated == 1:
        print(f"{email} is now ACTIVE.")
        return 0
    print(f"No PENDING_RESET account found for {email}; nothing changed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

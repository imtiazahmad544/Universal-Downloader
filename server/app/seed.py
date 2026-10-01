"""Bootstrap the initial super admin user.

Run from server/ with::

    python -m app.seed

Behavior:
    - Creates all tables first (init_db).
    - Reads ADMIN_USERNAME / ADMIN_PASSWORD from settings (.env or env vars).
    - If ADMIN_PASSWORD is empty/missing -> error and exit(1).
    - If ADMIN_PASSWORD is still the default "changeme" -> loud warning,
      then proceeds.
    - Idempotent: if the admin user already exists, does nothing (exit 0).
    - The password is NEVER printed.
"""

import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.security import hash_password
from app.models import User
from app.models.models import USER_ACTIVE


def main() -> None:
    init_db()

    username = settings.ADMIN_USERNAME.strip()
    password = settings.ADMIN_PASSWORD

    if not username:
        print("error: ADMIN_USERNAME is empty or missing; cannot seed admin user.", file=sys.stderr)
        sys.exit(1)
    if not password:
        print("error: ADMIN_PASSWORD is empty or missing; cannot seed admin user.", file=sys.stderr)
        sys.exit(1)
    if password == "changeme":
        print(
            "WARNING: ADMIN_PASSWORD is still the insecure default 'changeme'. "
            "Set a strong password via the ADMIN_PASSWORD environment variable or .env file."
        )

    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            print(f"super admin '{username}' already exists; nothing to do.")
            return

        admin = User(
            username=username,
            role="super_admin",
            customer_id=None,
            password_hash=hash_password(password),
            status=USER_ACTIVE,
        )
        db.add(admin)
        db.commit()
        print(f"super admin '{username}' created.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

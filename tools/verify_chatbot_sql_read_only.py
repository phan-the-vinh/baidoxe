"""Read-only SQL preflight for the manager chatbot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import create_app
from app.config import database_url
from app.extensions import db
from app.models import MonthlyPass, Payment, Slot, Visit


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "chatbot-sql-read-only-preflight",
            "SQLALCHEMY_DATABASE_URI": database_url(),
            "WTF_CSRF_ENABLED": False,
        }
    )
    with app.app_context():
        database = db.session.scalar(text("SELECT DB_NAME()"))
        state = db.session.execute(
            text("SELECT state_desc FROM sys.databases WHERE name = DB_NAME()")
        ).scalar_one()
        codes = list(db.session.scalars(select(Slot.code).order_by(Slot.code)))
        counts = {
            "slots": db.session.scalar(select(func.count()).select_from(Slot)),
            "visits": db.session.scalar(select(func.count()).select_from(Visit)),
            "payments": db.session.scalar(select(func.count()).select_from(Payment)),
            "monthly_passes": db.session.scalar(
                select(func.count()).select_from(MonthlyPass)
            ),
        }
        result = {
            "database": database,
            "state": state,
            "slot_codes": codes,
            "slots_are_d01_d24": codes == [f"D{number:02d}" for number in range(1, 25)],
            "counts": counts,
        }
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if database != "ParkingAI" or state != "ONLINE" or not result["slots_are_d01_d24"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

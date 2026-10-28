"""Read-only compatibility smoke test against the current ParkingAI database."""

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, inspect, select, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    load_dotenv(ROOT / ".env")
    from app import create_app
    from app.config import database_url
    from app.extensions import db
    from app.models import Payment, Slot, User, Visit
    from app.services.parking_layout import current_layout

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "legacy-read-only-smoke-check",
            "SQLALCHEMY_DATABASE_URI": database_url(),
            "WTF_CSRF_ENABLED": False,
            "TEST_ENVIRONMENT_LABEL": "SQL Server · ParkingAI · chỉ đọc",
        }
    )
    with app.app_context():
        require(db.session.scalar(text("SELECT DB_NAME()")) == "ParkingAI", "Sai database đích.")
        require(db.session.scalar(text("SELECT state_desc FROM sys.databases WHERE name=DB_NAME()")) == "ONLINE", "ParkingAI không ONLINE.")
        layout = current_layout()
        require(layout["name"] == "LEGACY_AB20" and layout["capacity"] == 20, "ParkingAI không còn baseline A01-B10.")
        require(not inspect(db.engine).has_table("parking_capacity_history"), "ParkingAI thật đã có thay đổi schema ngoài dự kiến.")
        require(db.session.scalar(select(func.count()).select_from(Visit)) == 4, "Số lượt gửi ParkingAI đã thay đổi.")
        require(db.session.scalar(select(func.count()).select_from(Payment)) == 5, "Số thanh toán ParkingAI đã thay đổi.")
        require(db.session.scalar(select(func.count()).select_from(Slot)) == 20, "Số ô ParkingAI đã thay đổi.")

        manager_id = db.session.scalar(select(User.id).where(User.role == "MANAGER").order_by(User.id))
        require(manager_id is not None, "Không có MANAGER để kiểm tra giao diện.")
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(manager_id)
            session["_fresh"] = True
        for path in ["/", "/slots", "/entry", "/exit", "/visits", "/monthly", "/rates", "/areas", "/statistics", "/vision", "/ai-demo"]:
            response = client.get(path)
            require(response.status_code == 200, f"Trang {path} trả HTTP {response.status_code}.")
        slots = client.get("/api/slots").get_json()
        require(slots["summary"]["capacity"] == 20, "Fallback sức chứa legacy không phải 20.")
        require([item["code"] for item in slots["slots"]] == [f"{area}{number:02d}" for area in "AB" for number in range(1, 11)], "API legacy không giữ A01-B10.")
        db.session.rollback()
        db.engine.dispose()

    print("DATABASE=ParkingAI")
    print("ONLINE_AND_COUNTS=OK")
    print("LEGACY_AB20_READ_ONLY_COMPATIBILITY=OK")
    print("WEB_GET_PAGES=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

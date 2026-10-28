"""Acceptance check for the D01-D24 web build against the isolated SQL clone.

The script never changes .env.  All write-path checks run inside an outer
transaction that is rolled back, then a fresh connection verifies exact
baseline rows were restored.
"""

import io
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[1]
TARGET_DATABASE = "ParkingAI_MigrationTest_20260924"
sys.path.insert(0, str(ROOT))


def odbc_value(value):
    return "{" + str(value).replace("}", "}}") + "}"


def test_database_url():
    values = {
        "DRIVER": os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        "SERVER": os.getenv("DB_SERVER", r"localhost\SQLEXPRESS"),
        "DATABASE": TARGET_DATABASE,
        "Encrypt": os.getenv("DB_ENCRYPT", "yes"),
        "TrustServerCertificate": os.getenv("DB_TRUST_SERVER_CERTIFICATE", "yes"),
        "Connection Timeout": "8",
    }
    if os.getenv("DB_TRUSTED_CONNECTION", "true").lower() == "true":
        values["Trusted_Connection"] = "yes"
    else:
        values["UID"] = os.getenv("DB_USER", "")
        values["PWD"] = os.getenv("DB_PASSWORD", "")
    return URL.create(
        "mssql+pyodbc",
        query={"odbc_connect": ";".join(f"{key}={odbc_value(value)}" for key, value in values.items())},
    )


def rows(connection, table, columns, order_by):
    query = f"SELECT {columns} FROM dbo.{table} ORDER BY {order_by}"
    return tuple(tuple(row) for row in connection.execute(text(query)))


def database_snapshot(connection):
    return {
        "areas": rows(connection, "areas", "code, name, description", "code"),
        "slots": rows(connection, "slots", "code, area_code, state, needs_review", "code"),
        "visits": rows(
            connection,
            "visits",
            "id, ticket_code, plate, slot_code, entered_at, exited_at, ticket_type, "
            "monthly_period_id, rate_id, created_by, exited_by, amount, fee_breakdown, "
            "lost_ticket, verification_note",
            "id",
        ),
        "payments": rows(
            connection, "payments", "id, visit_id, period_id, amount, kind, paid_at, cashier_id", "id"
        ),
        "audit_logs": rows(connection, "audit_logs", "id, actor_id, action, detail, created_at", "id"),
        "monthly_passes": rows(connection, "monthly_passes", "id, code, plate, owner_name, created_at", "id"),
        "pass_periods": rows(connection, "pass_periods", "id, pass_id, start_date, end_date, amount", "id"),
        "rates": rows(connection, "rates", "id, turn_fee, night_fee, monthly_fee, created_at, created_by", "id"),
        "site_lock": rows(connection, "site_lock", "id, revision", "id"),
        "capacity_history": rows(connection, "parking_capacity_history", "effective_at, capacity, reason", "effective_at"),
    }


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def login_as(client, user_id):
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def main():
    load_dotenv(ROOT / ".env")
    from app import create_app
    from flask import g
    from app.extensions import db
    from app.models import Observation, ParkingCapacityHistory, Payment, Slot, User, Visit
    from app.services.analytics import capacity_at, snapshot
    from app.services.parking_layout import current_layout

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "isolated-d24-acceptance-check-only",
            "SQLALCHEMY_DATABASE_URI": test_database_url(),
            "WTF_CSRF_ENABLED": False,
            "TEST_ENVIRONMENT_LABEL": f"SQL Server · {TARGET_DATABASE}",
        }
    )

    with app.app_context():
        engine = db.engine
        with engine.connect() as check:
            require(check.scalar(text("SELECT DB_NAME()")) == TARGET_DATABASE, "Sai database đích.")
            require(check.scalar(text("SELECT state_desc FROM sys.databases WHERE name=DB_NAME()")) == "ONLINE", "Database thử không ONLINE.")
            baseline = database_snapshot(check)
        require(len(baseline["slots"]) == 24, "Database thử không có đúng 24 ô.")
        require(tuple(row[0] for row in baseline["slots"]) == tuple(f"D{i:02d}" for i in range(1, 25)), "Mã ô không phải D01-D24.")
        require(len(baseline["visits"]) == 4 and len(baseline["payments"]) == 5, "Baseline nghiệp vụ khác kết quả migration đã duyệt.")

        connection = engine.connect()
        outer = connection.begin()
        default_engine = db.engines[None]
        db.engines[None] = connection
        db.session.remove()
        db.session.configure(bind=connection, join_transaction_mode="create_savepoint")
        try:
            layout = current_layout()
            require(layout["name"] == "D24" and not layout["legacy_cnn_write_enabled"], "Nhận dạng layout D24 thất bại.")
            require(snapshot()["capacity"] == 24, "Snapshot không dùng sức chứa 24.")
            history = list(db.session.scalars(select(ParkingCapacityHistory).order_by(ParkingCapacityHistory.effective_at)))
            require(len(history) >= 2 and history[0].capacity == 20 and history[-1].capacity == 24, "Lịch sử sức chứa 20/24 không hợp lệ.")
            require(capacity_at(history[-1].effective_at - timedelta(microseconds=1)) == 20, "Sức chứa trước migration không phải 20.")
            require(capacity_at(history[-1].effective_at) == 24, "Sức chứa sau migration không phải 24.")

            manager_id = db.session.scalar(select(User.id).where(User.role == "MANAGER").order_by(User.id))
            require(manager_id is not None, "Database thử không có tài khoản MANAGER.")
            client = app.test_client()
            login_as(client, manager_id)

            pages = ["/", "/slots", "/entry", "/exit", "/visits", "/monthly", "/rates", "/areas", "/statistics", "/vision", "/ai-demo"]
            for path in pages:
                response = client.get(path)
                require(response.status_code == 200, f"Trang {path} trả HTTP {response.status_code}.")

            slots_response = client.get("/api/slots")
            require(slots_response.status_code == 200, "API danh sách ô thất bại.")
            slot_payload = slots_response.get_json()
            require(slot_payload["summary"]["capacity"] == 24, "API vẫn báo sức chứa khác 24.")
            require([item["code"] for item in slot_payload["slots"]] == [f"D{i:02d}" for i in range(1, 25)], "API không trả D01-D24.")

            old_visit = client.get("/visits/1002")
            require(old_visit.status_code == 200 and b"D12" in old_visit.data, "Lượt lịch sử B02 chưa hiển thị D12.")

            plate = "99Z99999"
            entry = client.post(
                "/api/visits",
                json={"plate": plate, "slot_code": "D21", "plate_confirmed": True, "slot_confirmed": True},
            )
            require(entry.status_code == 200, f"Xe vào D21 thất bại: HTTP {entry.status_code}.")
            require(outer.is_active, "Route xe vào đã commit vượt transaction kiểm thử.")
            visit_id = entry.get_json()["visit_id"]
            search = client.get(f"/visits?q={plate}")
            require(search.status_code == 200 and plate.encode() in search.data, "Tra cứu lượt gửi thất bại.")
            quote = client.get(f"/api/visits/{visit_id}/quote")
            require(quote.status_code == 200, "Tính phí lượt gửi thất bại.")
            amount = quote.get_json()["fees"]["total"]
            checkout = client.post(
                f"/api/visits/{visit_id}/checkout",
                json={"quoted_amount": amount, "vehicle_confirmed": True, "paid_confirmed": True},
            )
            require(checkout.status_code == 200 and outer.is_active, "Xe ra/thanh toán thất bại hoặc vượt transaction kiểm thử.")

            monthly_plate = "98Z99998"
            monthly = client.post(
                "/api/monthly",
                json={
                    "plate": monthly_plate,
                    "owner_name": "Kiểm thử D24",
                    "start_date": str(date.today()),
                    "end_date": str(date.today() + timedelta(days=30)),
                    "amount": 500000,
                    "paid_confirmed": True,
                },
            )
            require(monthly.status_code == 200 and outer.is_active, "Tạo vé tháng trong transaction thử thất bại.")

            latest_rate = db.session.execute(
                text("SELECT TOP (1) turn_fee, night_fee, monthly_fee FROM dbo.rates ORDER BY id DESC")
            ).one()
            rate_count = db.session.scalar(select(func.count()).select_from(db.metadata.tables["rates"]))
            rate_update = client.post(
                "/api/rates",
                json={
                    "turn_fee": latest_rate.turn_fee,
                    "night_fee": latest_rate.night_fee,
                    "monthly_fee": latest_rate.monthly_fee or 0,
                },
            )
            require(rate_update.status_code == 200, "Cập nhật bảng giá trong transaction thử thất bại.")
            require(db.session.scalar(select(func.count()).select_from(db.metadata.tables["rates"])) == rate_count + 1, "Bảng giá mới không được lưu đúng.")

            create_staff = client.post(
                "/api/users",
                json={"username": "d24verify", "full_name": "Kiểm thử D24", "password": "d24-test-password"},
            )
            require(create_staff.status_code == 200, "Không tạo được tài khoản STAFF thử trong transaction.")
            staff_client = app.test_client()
            g.pop("_login_user", None)
            login = staff_client.post("/login", data={"username": "d24verify", "password": "d24-test-password"})
            require(login.status_code == 302, "Đăng nhập STAFF thử thất bại.")
            g.pop("_login_user", None)
            staff_rates = staff_client.get("/rates")
            require(staff_rates.status_code == 403, f"Phân quyền STAFF/MANAGER bị sai: HTTP {staff_rates.status_code}.")
            g.pop("_login_user", None)
            require(staff_client.get("/slots").status_code == 200, "STAFF không mở được trang vận hành.")

            observations_before = db.session.scalar(select(func.count()).select_from(Observation))
            vision = client.post("/api/vision/parking", data={"image": (io.BytesIO(b"not-used"), "frame.jpg")})
            require(vision.status_code == 409 and "20 ROI" in vision.get_json()["error"], "CNN cũ không bị khóa trên D24.")
            observations_after = db.session.scalar(select(func.count()).select_from(Observation))
            require(observations_after == observations_before, "CNN cũ đã ghi observation trên D24.")

            require(db.session.scalar(select(func.count()).select_from(Visit)) == 5, "Xe vào/ra thử không tạo đúng một lượt.")
            require(db.session.scalar(select(func.count()).select_from(Payment)) == 7, "Thanh toán lượt/vé tháng thử không đúng.")
        finally:
            db.session.remove()
            if outer.is_active:
                outer.rollback()
            db.engines[None] = default_engine
            connection.close()

        with engine.connect() as check:
            require(database_snapshot(check) == baseline, "Rollback không khôi phục chính xác dữ liệu baseline.")
            require(check.scalar(text("SELECT COUNT(*) FROM dbo.visits WHERE plate IN ('99Z99999','98Z99998')")) == 0, "Dữ liệu kiểm thử còn tồn tại.")
        engine.dispose()

    print("DATABASE=ParkingAI_MigrationTest_20260924")
    print("LAYOUT=D24")
    print("PAGES_AND_PERMISSIONS=OK")
    print("CHECKIN_CHECKOUT_PAYMENT_MONTHLY_HISTORY=OK")
    print("CAPACITY_HISTORY_20_TO_24=OK")
    print("LEGACY_CNN_WRITE_BLOCK=OK")
    print("OUTER_TRANSACTION_ROLLBACK=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

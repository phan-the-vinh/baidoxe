"""Read-only post-cutover acceptance checks for the production ParkingAI database."""

import io
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import g
from sqlalchemy import func, select, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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
    from app.config import database_url
    from app.extensions import db
    from app.models import AuditLog, Observation, ParkingCapacityHistory, Payment, Slot, User, Visit
    from app.services.analytics import capacity_at, snapshot
    from app.services.parking_layout import current_layout

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "production-d24-read-only-check",
            "SQLALCHEMY_DATABASE_URI": database_url(),
            "WTF_CSRF_ENABLED": False,
            "TEST_ENVIRONMENT_LABEL": "SQL Server · ParkingAI · hậu kiểm chỉ đọc",
        }
    )
    with app.app_context():
        require(db.session.scalar(text("SELECT DB_NAME()")) == "ParkingAI", "Sai database đích.")
        layout = current_layout()
        require(layout["name"] == "D24" and layout["slot_codes"] == tuple(f"D{i:02d}" for i in range(1, 25)), "Layout không phải D01-D24.")
        baseline = {
            "visits": db.session.scalar(select(func.count()).select_from(Visit)),
            "payments": db.session.scalar(select(func.count()).select_from(Payment)),
            "audits": db.session.scalar(select(func.count()).select_from(AuditLog)),
            "observations": db.session.scalar(select(func.count()).select_from(Observation)),
        }
        require(baseline == {"visits": 4, "payments": 5, "audits": 10, "observations": 0}, "Baseline nghiệp vụ sau migration không đúng.")
        require(snapshot()["capacity"] == 24, "Thống kê hiện tại không dùng sức chứa 24.")
        history = list(db.session.scalars(select(ParkingCapacityHistory).order_by(ParkingCapacityHistory.effective_at)))
        require(len(history) == 2 and history[0].capacity == 20 and history[1].capacity == 24, "Lịch sử sức chứa không đúng 20→24.")
        require(capacity_at(history[1].effective_at - timedelta(microseconds=1)) == 20, "Sức chứa trước cutover không phải 20.")
        require(capacity_at(history[1].effective_at) == 24, "Sức chứa sau cutover không phải 24.")

        manager_id = db.session.scalar(select(User.id).where(User.role == "MANAGER").order_by(User.id))
        staff_id = db.session.scalar(select(User.id).where(User.role == "STAFF").order_by(User.id))
        require(manager_id is not None and staff_id is not None, "Thiếu MANAGER hoặc STAFF.")
        manager = app.test_client()
        login_as(manager, manager_id)
        with manager.session_transaction() as session:
            session["vision_job_id"] = "842a1df095ad49e48a6b8bed36791dec"
        for path in ["/", "/slots", "/entry", "/exit", "/visits", "/monthly", "/rates", "/areas", "/statistics", "/vision", "/ai-demo"]:
            response = manager.get(path)
            require(response.status_code == 200, f"Trang {path} trả HTTP {response.status_code}.")

        payload = manager.get("/api/slots").get_json()
        require(payload["summary"]["capacity"] == 24, "API không báo sức chứa 24.")
        require([item["code"] for item in payload["slots"]] == [f"D{i:02d}" for i in range(1, 25)], "API không trả đúng D01-D24.")
        demo_page = manager.get("/ai-demo")
        require(
            b"842a1df095ad49e48a6b8bed36791dec" not in demo_page.data
            and b"/vision/video" not in demo_page.data
            and b"/ai-demo/video?v=" in demo_page.data,
            "/ai-demo còn phụ thuộc manifest/video của /vision.",
        )
        expected_visits = {1: "D19", 2: "D01", 1002: "D12", 2002: "D12"}
        for visit_id, slot_code in expected_visits.items():
            page = manager.get(f"/visits/{visit_id}")
            require(page.status_code == 200 and slot_code.encode() in page.data, f"Lượt {visit_id} không hiển thị {slot_code}.")

        vision_status = manager.get("/api/vision/status").get_json()
        require(not vision_status["legacy_classifier"]["ready"], "CNN 20 ROI vẫn được bật trên D24.")
        blocked = manager.post("/api/vision/parking", data={"image": (io.BytesIO(b"unused"), "frame.jpg")})
        require(blocked.status_code == 409 and "20 ROI" in blocked.get_json()["error"], "API CNN cũ không chặn ghi.")
        require(manager.post("/api/vision/plate", data={"image": (io.BytesIO(b"invalid"), "plate.jpg")}).status_code == 400, "Route OCR không còn xử lý upload độc lập.")
        require(manager.get("/api/ai-demo/states").status_code == 200, "API trạng thái /ai-demo thất bại.")

        staff = app.test_client()
        login_as(staff, staff_id)
        g.pop("_login_user", None)
        require(staff.get("/slots").status_code == 200, "STAFF không mở được trang vận hành.")
        g.pop("_login_user", None)
        require(staff.get("/rates").status_code == 403, "Phân quyền STAFF/MANAGER không đúng.")

        current = {
            "visits": db.session.scalar(select(func.count()).select_from(Visit)),
            "payments": db.session.scalar(select(func.count()).select_from(Payment)),
            "audits": db.session.scalar(select(func.count()).select_from(AuditLog)),
            "observations": db.session.scalar(select(func.count()).select_from(Observation)),
        }
        require(current == baseline, "Hậu kiểm web đã làm thay đổi dữ liệu nghiệp vụ.")
        db.session.rollback()
        db.engine.dispose()

    print("DATABASE=ParkingAI")
    print("LAYOUT_D01_D24=OK")
    print("WEB_MANAGER_STAFF_READ_ONLY=OK")
    print("VISIT_MAPPING_AND_CAPACITY_HISTORY=OK")
    print("AI_DEMO_AND_VISION_GUARDS=OK")
    print("BUSINESS_COUNTS_UNCHANGED=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

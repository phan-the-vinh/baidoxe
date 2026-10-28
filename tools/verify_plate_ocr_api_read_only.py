"""Read-only API/UI verification for the plate OCR workflow."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import cv2
from dotenv import load_dotenv
from sqlalchemy import event, func, select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import create_app
from app.config import database_url
from app.extensions import db
from app.models import MonthlyPass, Payment, Slot, User, Visit
from app.services import plate_ocr
from verify_plate_ocr import plate_scene


def counts() -> dict:
    return {
        "slots": db.session.scalar(select(func.count()).select_from(Slot)),
        "visits": db.session.scalar(select(func.count()).select_from(Visit)),
        "payments": db.session.scalar(select(func.count()).select_from(Payment)),
        "monthly_passes": db.session.scalar(select(func.count()).select_from(MonthlyPass)),
    }


def login_as(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def jpeg_bytes():
    ok, encoded = cv2.imencode(".jpg", plate_scene(), [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise RuntimeError("Could not encode controlled test image")
    return encoded.tobytes()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "plate-ocr-read-only-verification",
        "SQLALCHEMY_DATABASE_URI": database_url(),
        "WTF_CSRF_ENABLED": False,
    })
    writes: list[str] = []
    with app.app_context():
        before = counts()
        manager_id = db.session.scalar(
            select(User.id).where(User.role == "MANAGER").order_by(User.id)
        )
        if manager_id is None:
            raise RuntimeError("Missing MANAGER account")

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP", "CREATE"}:
                writes.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        original_command = os.environ.get("TESSERACT_CMD")
        try:
            client = app.test_client()
            login_as(client, manager_id)
            entry = client.get("/entry")
            html = entry.get_data(as_text=True)
            required_ui = ("AI đề xuất", "Ô chỉnh sửa biển số", "Xác nhận biển số", "Ảnh gốc đã chọn")
            if entry.status_code != 200 or not all(label in html for label in required_ui):
                raise RuntimeError("Entry OCR confirmation UI is incomplete")

            response = client.post(
                "/api/vision/plate",
                data={"image": (io.BytesIO(jpeg_bytes()), "controlled-clear.jpg")},
                content_type="multipart/form-data",
            )
            payload = response.get_json()
            required_keys = {
                "ok", "plate", "normalized_plate", "raw_text", "confidence",
                "needs_confirmation", "crop_image", "bbox", "message",
            }
            if response.status_code != 200 or not required_keys.issubset(payload):
                raise RuntimeError(f"Unexpected OCR API response: {response.status_code} {payload}")
            if payload["needs_confirmation"] is not True:
                raise RuntimeError("OCR suggestion was not marked for confirmation")

            os.environ["TESSERACT_CMD"] = str(ROOT / "missing-tesseract.exe")
            plate_ocr._verify_tesseract.cache_clear()
            unavailable = client.post(
                "/api/vision/plate",
                data={"image": (io.BytesIO(jpeg_bytes()), "controlled-clear.jpg")},
                content_type="multipart/form-data",
            )
            unavailable_payload = unavailable.get_json()
            if unavailable.status_code != 503 or "TESSERACT_CMD" not in unavailable_payload.get("error", ""):
                raise RuntimeError("Missing Tesseract did not return a friendly 503")

            after = counts()
            if before != after or writes:
                raise RuntimeError(
                    f"SQL write detected: before={before}, after={after}, writes={len(writes)}"
                )
        finally:
            if original_command is None:
                os.environ.pop("TESSERACT_CMD", None)
            else:
                os.environ["TESSERACT_CMD"] = original_command
            plate_ocr._verify_tesseract.cache_clear()
            event.remove(db.engine, "before_cursor_execute", capture)
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()

    print(json.dumps({
        "api_status": 200,
        "api_result": {
            "ok": payload["ok"],
            "plate": payload["plate"],
            "raw_text": payload["raw_text"],
            "confidence": payload["confidence"],
            "needs_confirmation": payload["needs_confirmation"],
            "has_crop": bool(payload["crop_image"]),
        },
        "missing_tesseract_status": unavailable.status_code,
        "missing_tesseract_message": unavailable_payload["error"],
        "ui_confirmation_controls": True,
        "counts_before_after": before,
        "sql_writes": len(writes),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

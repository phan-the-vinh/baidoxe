import io
from pathlib import Path
import cv2
import numpy as np
from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog, Observation, Payment, Visit, Slot
from tools.ocr_plate_crop_worker import recognize_exact_crop


def enter(client, plate="30A12345", slot="D01"):
    return client.post(
        "/api/visits",
        json={
            "plate": plate,
            "slot_code": slot,
            "plate_confirmed": True,
            "slot_confirmed": True,
        },
    )


def test_plate_api_returns_ocr_and_does_not_write(manager, app, monkeypatch):
    test_crop = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    expected = {
        "ok": True,
        "message": "Phát hiện 1 xe và 1 biển số.",
        "image_width": 320,
        "image_height": 180,
        "car_count": 1,
        "plate_count": 1,
        "fallback_used": False,
        "fallback_reasons": [],
        "fallback_plate_count": 0,
        "vehicles": [{
            "vehicle_index": 1,
            "vehicle_bbox": [10, 20, 300, 170],
            "vehicle_confidence": 0.91,
            "plate_detected": True,
            "plate_bbox": [120, 110, 210, 145],
            "plate_confidence": 0.94,
            "plate_detection_source": "vehicle_roi",
            "plate_crop_original": test_crop,
            "plate_crop_enhanced": test_crop,
            "plate": "30A-123.45",
            "raw_text": "30A 123.45",
            "ocr_confidence": 0.95,
            "ocr_layout": "1-line",
        }],
        "plate": "30A-123.45",
        "raw_text": "30A 123.45",
        "ocr_confidence": 0.95,
    }
    monkeypatch.setattr(
        "app.services.plate_detection.detect_vehicles_and_plates", lambda frame: expected
    )
    with app.app_context():
        before = {
            model.__tablename__: db.session.scalar(select(func.count()).select_from(model))
            for model in (Visit, Payment, Observation, AuditLog)
        }
    encoded = cv2.imencode(".jpg", np.zeros((180, 320, 3), np.uint8))[1].tobytes()
    response = manager.post(
        "/api/vision/plate", data={"image": (io.BytesIO(encoded), "frame.jpg")}
    )
    assert response.status_code == 200
    assert response.json["plate"] == "30A-123.45"
    assert response.json["vehicles"][0]["plate"] == "30A-123.45"
    with app.app_context():
        after = {
            model.__tablename__: db.session.scalar(select(func.count()).select_from(model))
            for model in (Visit, Payment, Observation, AuditLog)
        }
    assert after == before


def test_entry_and_exit_ui_has_ocr_widget(manager):
    for url in ("/entry", "/exit"):
        page = manager.get(url)
        assert page.status_code == 200
        html = page.data.decode("utf-8")
        assert "plate-detection-widget" in html
        assert "ocr-plate-card" in html
        assert "ocr-recognized-badge" in html
        assert "ocr-plate-input" in html
        assert "ocr-apply-btn" in html
        assert "ocr-lookup-btn" in html
        assert "ocr-lookup-feedback" in html

    script = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "ocr-lookup-btn" in script
    assert "ocr-apply-btn" in script
    assert "/api/visits/lookup" in script


def test_plate_video_window_route_uses_all_frames(manager, monkeypatch):
    captured = {}

    def fake_window(uploads, current_index):
        captured.update(count=len(uploads), current_index=current_index)
        return {"ok": True, "vehicles": [], "plate_count": 0}

    monkeypatch.setattr("app.services.vision.plate_frame_window", fake_window)
    image = cv2.imencode(".jpg", np.zeros((32, 48, 3), np.uint8))[1].tobytes()
    response = manager.post(
        "/api/vision/plate",
        data={
            "frames": [(io.BytesIO(image), f"frame_{index}.jpg") for index in range(3)],
            "current_index": "1",
        },
    )
    assert response.status_code == 200
    assert captured == {"count": 3, "current_index": 1}


def test_visit_lookup_api(manager, app):
    # Missing plate parameter
    resp_empty = manager.get("/api/visits/lookup")
    assert resp_empty.status_code == 400

    # Non-existent plate
    resp_none = manager.get("/api/visits/lookup?plate=99Z99999")
    assert resp_none.status_code == 200
    assert resp_none.json["found"] is False
    assert "Không tìm thấy" in resp_none.json["message"]

    # Active vehicle in lot
    res_entry = enter(manager, plate="51G88899", slot="D01")
    assert res_entry.status_code == 200

    # Query with punctuation and spacing: "51G - 888.99"
    resp_found = manager.get("/api/visits/lookup?plate=51G+-+888.99")
    assert resp_found.status_code == 200
    assert resp_found.json["found"] is True
    assert resp_found.json["plate"] == "51G88899"
    assert resp_found.json["visit"]["slot_code"] == "D01"
    assert "exit_url" in resp_found.json["visit"]
    assert "detail_url" in resp_found.json["visit"]

    # Check out vehicle and verify it is no longer returned as active
    visit_id = res_entry.json["visit_id"]
    quote = manager.get(f"/api/visits/{visit_id}/quote")
    checkout_res = manager.post(
        f"/api/visits/{visit_id}/checkout",
        json={
            "quoted_amount": quote.json.get("fees", {}).get("total", 0),
            "vehicle_confirmed": True,
            "paid_confirmed": True,
        },
    )
    assert checkout_res.status_code == 200

    # Query again after exit
    resp_after = manager.get("/api/visits/lookup?plate=51G88899")
    assert resp_after.status_code == 200
    assert resp_after.json["found"] is False


def test_ocr_crop_worker_clear_vs_blank():
    import pytest
    try:
        import pytesseract
    except ImportError:
        pytest.skip("pytesseract is installed in parking_ai runtime env, skipping in runner without pytesseract")

    # Synthetic clear 1-line plate
    plate_img = np.ones((70, 240, 3), dtype=np.uint8) * 255
    cv2.rectangle(plate_img, (2, 2), (237, 67), (0, 0, 0), 2)
    cv2.putText(
        plate_img,
        "30A-123.45",
        (15, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )

    res = recognize_exact_crop(plate_img, None)
    assert res.get("plate") is not None
    assert "30A" in res["plate"]
    assert res.get("ocr_confidence") is not None

    # Blank/black image
    blank_img = np.zeros((70, 240, 3), dtype=np.uint8)
    blank_res = recognize_exact_crop(blank_img, None)
    assert blank_res.get("plate") is None

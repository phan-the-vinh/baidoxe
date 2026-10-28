from sqlalchemy import event

from app.extensions import db
from app.services.demo_ai_playback import (
    DEMO_OUTPUT_DIR,
    load_demo_bundle,
    load_demo_timeline,
    timeline_state_at,
)


def _slot_state(payload, seconds, slot_id):
    snapshot = timeline_state_at(payload, seconds)
    return snapshot["states"][payload["slot_ids"].index(slot_id)]


def test_ai_demo_requires_login(client):
    page = client.get("/ai-demo")
    video = client.get("/ai-demo/video", headers={"Range": "bytes=0-31"})
    states = client.get("/api/ai-demo/states")
    assert page.status_code == 302
    assert "/login" in page.headers["Location"]
    assert video.status_code == 302
    assert states.status_code == 401


def test_manager_can_open_demo_and_complete_timeline(manager):
    page = manager.get("/ai-demo")
    assert page.status_code == 200
    assert "Video demo — Kết quả YOLO đã xử lý trước" in page.text
    assert "không cập nhật SQL Server" in page.text
    assert page.text.count("data-demo-slot=") == 24

    response = manager.get("/api/ai-demo/states")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["slot_ids"] == [f"D{number:02d}" for number in range(1, 25)]
    assert payload["total_slots"] == 24
    assert payload["total_frames"] == 1623
    assert payload["fps"] == 30.0
    assert payload["duration_seconds"] == 54.1
    assert len(payload["timeline"]) == 14
    assert payload["timeline"][0]["states"] == ["UNKNOWN"] * 24
    assert all(sum(entry["counts"].values()) == 24 for entry in payload["timeline"])


def test_ai_demo_is_isolated_from_vision_job_manifest(manager):
    wrong_job_id = "842a1df095ad49e48a6b8bed36791dec"
    with manager.session_transaction() as session:
        session["vision_job_id"] = wrong_job_id

    page = manager.get("/ai-demo")
    states = manager.get("/api/ai-demo/states")
    assert page.status_code == 200
    assert states.status_code == 200
    assert wrong_job_id not in page.text
    assert "/vision/video" not in page.text
    assert "/ai-demo/video?v=" in page.text
    payload = states.get_json()
    assert payload["total_frames"] == 1623
    assert payload["video"] == {
        "frames": 1623,
        "fps": 30.0,
        "duration_seconds": 54.1,
        "width": 1920,
        "height": 1080,
    }
    assert DEMO_OUTPUT_DIR.name == "full_video_d16_logic_fix_conf040"


def test_demo_bundle_validates_all_four_artifacts():
    payload = load_demo_bundle()
    assert payload["total_frames"] == 1623
    assert payload["duration_seconds"] == 54.1
    assert payload["video"]["frames"] == payload["total_frames"]
    assert _slot_state(payload, 33.433333, "D16") == "EMPTY"
    assert _slot_state(payload, 44.4, "D03") == "EMPTY"
    assert _slot_state(payload, 47.333333, "D03") == "OCCUPIED"


def test_staff_has_same_read_only_demo_access(staff):
    page = staff.get("/ai-demo")
    states = staff.get("/api/ai-demo/states")
    assert page.status_code == 200
    assert "/ai-demo" in page.text
    assert states.status_code == 200


def test_video_supports_authenticated_byte_ranges(manager):
    response = manager.get(
        "/ai-demo/video", headers={"Range": "bytes=0-1023"}, buffered=False
    )
    try:
        assert response.status_code == 206
        assert response.mimetype == "video/mp4"
        assert response.headers["Accept-Ranges"] == "bytes"
        assert response.headers["Content-Range"].startswith("bytes 0-1023/")
        assert len(response.get_data()) == 1024
    finally:
        response.close()


def test_playback_checkpoints_seek_replay_and_end_use_csv_states():
    payload = load_demo_timeline()

    # Initial load and replay both return the first all-UNKNOWN snapshot.
    assert timeline_state_at(payload, 0)["counts"] == {
        "occupied": 0, "empty": 0, "unknown": 24,
    }
    assert timeline_state_at(payload, 0) == timeline_state_at(payload, 0)

    # Play/pause does not alter a state when currentTime has not crossed a transition.
    assert _slot_state(payload, 3.29, "D01") == "OCCUPIED"
    assert _slot_state(payload, 3.29, "D01") == "OCCUPIED"

    # Forward/backward seeks select the last real CSV state at or before currentTime.
    assert _slot_state(payload, 3.30, "D01") == "EMPTY"
    assert _slot_state(payload, 12.40, "D08") == "EMPTY"
    assert _slot_state(payload, 12.866667, "D01") == "OCCUPIED"
    assert _slot_state(payload, 24.033333, "D08") == "OCCUPIED"
    assert _slot_state(payload, 33.42, "D16") == "OCCUPIED"
    assert _slot_state(payload, 33.433333, "D16") == "EMPTY"
    assert _slot_state(payload, 2.0, "D01") == "OCCUPIED"

    end = timeline_state_at(payload, payload["duration_seconds"])
    assert end["counts"] == {"occupied": 22, "empty": 2, "unknown": 0}
    assert _slot_state(payload, payload["duration_seconds"], "D16") == "EMPTY"
    assert _slot_state(payload, payload["duration_seconds"], "D24") == "EMPTY"


def test_demo_get_requests_execute_no_sql_writes(app, staff):
    write_statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"}:
            write_statements.append(statement)

    with app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", capture)
    try:
        assert staff.get("/ai-demo").status_code == 200
        assert staff.get("/api/ai-demo/states").status_code == 200
        response = staff.get(
            "/ai-demo/video", headers={"Range": "bytes=0-31"}, buffered=False
        )
        assert response.status_code == 206
        response.close()
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert write_statements == []

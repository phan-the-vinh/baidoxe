import hashlib
import json

from app.services import vision_video


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timeline(frames=2):
    slot_ids = [f"D{number:02d}" for number in range(1, 25)]
    return {
        "schema_version": 1,
        "video": {
            "width": 3840,
            "height": 2160,
            "fps": 30.0,
            "frames": frames,
            "duration_seconds": frames / 30,
            "codec": "hevc",
            "sha256": "unused",
        },
        "coordinate_space": {"width": 3840, "height": 2160},
        "polygons": [
            {"id": slot_id, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}
            for slot_id in slot_ids
        ],
        "slot_ids": slot_ids,
        "states": ["OCCUPIED", "EMPTY", "UNKNOWN"],
        "total_slots": 24,
        "total_frames": frames,
        "fps": 30.0,
        "duration_seconds": frames / 30,
        "timeline": [
            {
                "frame": 0,
                "time": 0.0,
                "counts": {"occupied": 0, "empty": 0, "unknown": 24},
                "states": ["UNKNOWN"] * 24,
            }
        ],
    }


def test_vision_video_requires_login(client):
    assert client.get("/vision").status_code == 302
    assert client.get("/vision/video").status_code == 302
    assert client.get("/api/vision/video/status").status_code == 401
    assert client.get("/api/vision/video/states").status_code == 401


def test_vision_reports_data_only_yolo_pipeline(staff):
    page = staff.get("/vision")
    status = staff.get("/api/vision/status")

    assert page.status_code == 200
    assert "YOLOv8n" in page.text
    assert "Video gốc" in page.text
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["ready"] is True
    assert payload["training"] is False
    assert payload["annotated_video"] is False
    assert vision_video.UPLOAD_ROOT.name == "parking_analysis_uploads"
    assert vision_video.RESULT_ROOT.name == "parking_analysis_results"
    assert vision_video.MANIFEST_ROOT.name == "parking_analysis_manifests"


def test_completed_job_serves_original_video_and_canvas_timeline(
    staff, tmp_path, monkeypatch
):
    manifest_root = tmp_path / "manifests"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"immutable raw video bytes")
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(_timeline()), encoding="utf-8")

    monkeypatch.setattr(vision_video, "MANIFEST_ROOT", manifest_root)
    vision_video._jobs.clear()
    job_id = "a" * 32
    job = {
        "id": job_id,
        "owner_id": 2,
        "status": "complete",
        "progress_frames": 2,
        "total_frames": 2,
        "error": None,
        "metadata": {
            "width": 3840,
            "height": 2160,
            "fps": 30.0,
            "frames": 2,
            "duration_seconds": 2 / 30,
            "codec": "hevc",
        },
        "input_path": str(source),
        "input_size": source.stat().st_size,
        "input_sha256": _sha256(source),
        "timeline_path": str(timeline_path),
        "states_path": "unused",
        "transitions_path": "unused",
        "summary_path": "unused",
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    manifest_root.mkdir(parents=True)
    (manifest_root / f"{job_id}.json").write_text(
        json.dumps(job), encoding="utf-8"
    )
    with staff.session_transaction() as session:
        session["parking_analysis_job_id"] = job_id

    page = staff.get("/vision")
    states = staff.get("/api/vision/video/states")
    video = staff.get(
        "/vision/video", headers={"Range": "bytes=0-7"}, buffered=False
    )
    try:
        assert page.status_code == 200
        assert page.text.count("data-demo-slot=") == 24
        assert 'id="parking-overlay-canvas"' in page.text
        assert states.status_code == 200
        assert states.get_json()["vision_job_id"] == job_id
        assert "no-store" in states.headers["Cache-Control"]
        assert states.get_json()["total_frames"] == 2
        assert states.get_json()["polygons"][15]["id"] == "D16"
        assert video.status_code == 206
        assert video.mimetype == "video/mp4"
        assert video.headers["Accept-Ranges"] == "bytes"
        assert video.get_data() == b"immutabl"
    finally:
        video.close()
        vision_video._jobs.clear()


def test_timeline_rejects_invalid_state_total(tmp_path):
    payload = _timeline()
    payload["timeline"][0]["counts"]["unknown"] = 23
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        vision_video._validate_timeline(path, 2)
    except RuntimeError as exc:
        assert "không bằng 24" in str(exc)
    else:
        raise AssertionError("Invalid state total was accepted")

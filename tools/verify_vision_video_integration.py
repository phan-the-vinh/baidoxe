"""Verify raw-video upload, data-only analysis, and Canvas timeline on /vision."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.cli import seed_database
from app.extensions import db
from app.services.vision_video import get_job


DEFAULT_VIDEO = ROOT / "demo_videos" / "0922(1).mp4"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument(
        "--job-id",
        help="Verify an existing completed /vision job instead of processing again.",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    return parser.parse_args()


def transition_time(payload, slot_id, previous, current):
    index = payload["slot_ids"].index(slot_id)
    last = None
    for entry in payload["timeline"]:
        state = entry["states"][index]
        if last == previous and state == current:
            return entry["time"]
        last = state
    return None


def state_at(payload, slot_id, timestamp):
    index = payload["slot_ids"].index(slot_id)
    state = None
    for entry in payload["timeline"]:
        if float(entry["time"]) > timestamp:
            break
        state = entry["states"][index]
    return state


def main():
    args = parse_args()
    source = args.input.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    temporary = tempfile.TemporaryDirectory(prefix="vision-integration-")
    database = Path(temporary.name) / "test.db"
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "vision-integration-test-secret-only",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
            "WTF_CSRF_ENABLED": False,
        }
    )
    client = application.test_client()
    writes = []

    with application.app_context():
        db.create_all()
        seed_database("manager", "vision-test-only")
        assert client.post(
            "/login",
            data={"username": "manager", "password": "vision-test-only"},
        ).status_code == 302

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"}:
                writes.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            if args.job_id:
                job_id = args.job_id
                existing = get_job(job_id, 1)
                if existing is None or existing["status"] != "complete":
                    raise RuntimeError("The requested /vision job is not complete")
                selected = client.get(f"/vision?job={job_id}")
                assert selected.status_code == 200
                with client.session_transaction() as session:
                    assert session["parking_analysis_job_id"] == job_id
                print(f"job={job_id} status=complete (reused)", flush=True)
            else:
                with source.open("rb") as uploaded:
                    response = client.post(
                        "/api/vision/video",
                        data={"video": (uploaded, source.name)},
                        content_type="multipart/form-data",
                    )
                if response.status_code != 202:
                    raise RuntimeError(f"Upload failed: {response.status_code} {response.text}")
                public = response.get_json()["job"]
                job_id = public["id"]
                print(f"job={job_id} frames={public['total_frames']}", flush=True)

                last_report = None
                while True:
                    response = client.get("/api/vision/video/status")
                    if response.status_code != 200:
                        raise RuntimeError(f"Status failed: {response.status_code} {response.text}")
                    public = response.get_json()["job"]
                    report = (public["status"], public["progress_frames"])
                    if report != last_report:
                        print(
                            f"status={report[0]} progress={report[1]}/{public['total_frames']}",
                            flush=True,
                        )
                        last_report = report
                    if public["status"] == "complete":
                        break
                    if public["status"] == "failed":
                        raise RuntimeError(public["error"])
                    time.sleep(args.poll_seconds)

            states = client.get("/api/vision/video/states")
            assert states.status_code == 200
            payload = states.get_json()
            assert payload["vision_job_id"] == job_id
            assert "no-store" in states.headers["Cache-Control"]
            assert payload["total_frames"] == 1623
            assert all(sum(item["counts"].values()) == 24 for item in payload["timeline"])
            assert state_at(payload, "D13", 3.0) == "UNKNOWN"
            assert state_at(payload, "D13", 3.8) == "UNKNOWN"
            assert state_at(payload, "D13", 5.0) == "UNKNOWN"
            assert transition_time(payload, "D13", "UNKNOWN", "OCCUPIED") == 19.5
            assert transition_time(payload, "D16", "OCCUPIED", "EMPTY") == 33.433333
            assert transition_time(payload, "D03", "OCCUPIED", "EMPTY") == 44.4
            assert transition_time(payload, "D03", "EMPTY", "OCCUPIED") == 47.333333
            assert payload["timeline"][-1]["counts"] == {
                "occupied": 22,
                "empty": 2,
                "unknown": 0,
            }
            assert len(payload["polygons"]) == 24
            assert payload["coordinate_space"] == {"width": 3840, "height": 2160}

            page = client.get("/vision")
            assert page.status_code == 200
            assert page.data.count(b"data-demo-slot=") == 24
            assert b"data-demo-ai" in page.data
            assert b"parking-overlay-canvas" in page.data

            result_video = client.get(
                "/vision/video", headers={"Range": "bytes=0-1023"}, buffered=False
            )
            try:
                assert result_video.status_code == 206
                assert result_video.mimetype == "video/mp4"
                assert result_video.headers["Accept-Ranges"] == "bytes"
                assert len(result_video.get_data()) == 1024
                assert result_video.get_data() == source.read_bytes()[:1024]
            finally:
                result_video.close()

            job = get_job(job_id, 1)
            if job is None:
                raise RuntimeError("Completed job is unavailable")
            run_dir = Path(job["timeline_path"]).parent
            assert not any(path.suffix.lower() in {".mp4", ".webm", ".mov", ".avi"} for path in run_dir.iterdir())
            expected_files = {
                "analysis_summary.json",
                "timeline.json",
                "slot_states.csv",
                "state_transitions.csv",
                "detections.csv",
                "excluded_detections.csv",
            }
            assert {path.name for path in run_dir.iterdir()} == expected_files
            input_path = Path(job["input_path"])
            assert input_path.parent.name == "parking_analysis_uploads"
            assert input_path.read_bytes() == source.read_bytes()
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            assert job["input_sha256"] == source_hash
            summary = json.loads(
                (run_dir / "analysis_summary.json").read_text(encoding="utf-8")
            )
            assert summary["video"]["frames"] == 1623
            assert summary["video"]["fps"] == 30.0
            assert summary["processing"]["training_performed"] is False
            assert summary["processing"]["annotated_video_created"] is False
            assert summary["thresholds"]["minimum_axis_elongation"] == 1.25
            assert summary["thresholds"]["core_horizontal_inset"] == 0.15
            assert summary["thresholds"]["core_top_inset"] == 0.10
            assert summary["thresholds"]["core_bottom_inset"] == 0.25
            assert summary["thresholds"]["border_margin_ratio"] == 0.01
            assert summary["thresholds"]["ambiguous_seconds"] == 0.25
            assert summary["thresholds"]["ambiguous_frames"] == 8

            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "result_dir": str(run_dir),
                        "source_video_path": job["input_path"],
                        "source_bytes_preserved": True,
                        "annotated_video_created": False,
                        "d13_at_3_8": state_at(payload, "D13", 3.8),
                        "d13_occupied": transition_time(payload, "D13", "UNKNOWN", "OCCUPIED"),
                        "d16_empty": transition_time(payload, "D16", "OCCUPIED", "EMPTY"),
                        "d03_empty": transition_time(payload, "D03", "OCCUPIED", "EMPTY"),
                        "d03_occupied": transition_time(payload, "D03", "EMPTY", "OCCUPIED"),
                        "sql_writes": len(writes),
                    },
                    indent=2,
                ),
                flush=True,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
            db.session.remove()
            db.engine.dispose()
    temporary.cleanup()
    if writes:
        raise RuntimeError(f"Unexpected SQL writes during video workflow: {len(writes)}")


if __name__ == "__main__":
    main()

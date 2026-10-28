"""Verify /vision upload limits with CSRF enabled and without starting YOLO."""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.extensions import db
from app.models import User
from app.services import vision_video


SOURCE = ROOT / "demo_videos" / "0922(1).mp4"


def token_from(content: bytes, pattern: bytes) -> str:
    match = re.search(pattern, content)
    if not match:
        raise RuntimeError("CSRF token was not rendered")
    return match.group(1).decode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    source_size = SOURCE.stat().st_size
    if not 85_000_000 <= source_size <= 90_000_000:
        raise RuntimeError(f"Expected an 85-90 MB test video, received {source_size} bytes")

    temporary = tempfile.TemporaryDirectory(
        prefix="vision-upload-limit-", ignore_cleanup_errors=True
    )
    original_start = threading.Thread.start
    try:
        temp = Path(temporary.name)
        application = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "vision-upload-limit-test-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(temp / "test.db"),
                "WTF_CSRF_ENABLED": True,
            }
        )
        vision_video.UPLOAD_ROOT = temp / "uploads"
        vision_video.RESULT_ROOT = temp / "results"
        vision_video.MANIFEST_ROOT = temp / "manifests"
        vision_video._jobs.clear()
        threading.Thread.start = lambda _thread: None

        with application.app_context():
            db.create_all()
            user = User(username="employee", full_name="Employee", role="STAFF")
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()

        client = application.test_client()
        login_page = client.get("/login")
        login_token = token_from(
            login_page.data, rb'name="csrf_token" value="([^"]+)"'
        )
        login = client.post(
            "/login",
            data={
                "username": "employee",
                "password": "test-password",
                "csrf_token": login_token,
            },
        )
        if login.status_code != 302:
            raise RuntimeError(f"Login failed: {login.status_code}")

        page = client.get("/vision")
        csrf_token = token_from(
            page.data, rb'name="csrf-token" content="([^"]+)"'
        )
        headers = {"X-CSRFToken": csrf_token, "Accept": "application/json"}

        with SOURCE.open("rb") as source:
            accepted = client.post(
                "/api/vision/video",
                data={"video": (source, SOURCE.name)},
                content_type="multipart/form-data",
                headers=headers,
            )
        if accepted.status_code != 202:
            raise RuntimeError(
                f"85-90 MB upload failed: {accepted.status_code} {accepted.get_data(as_text=True)}"
            )
        job_id = accepted.get_json()["job"]["id"]
        uploaded = vision_video.UPLOAD_ROOT / f"{job_id}.mp4"
        if uploaded.stat().st_size != source_size or sha256(uploaded) != sha256(SOURCE):
            raise RuntimeError("Accepted upload is not byte-identical to the source")

        oversized = temp / "over-250mb.mp4"
        with SOURCE.open("rb") as source, oversized.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.seek(vision_video.MAX_VIDEO_BYTES)
            output.write(b"\0")
        with oversized.open("rb") as source:
            rejected = client.post(
                "/api/vision/video",
                data={"video": (source, oversized.name)},
                content_type="multipart/form-data",
                headers=headers,
            )
        error = rejected.get_json(silent=True) or {}
        if rejected.status_code != 413 or "250 MB" not in error.get("error", ""):
            raise RuntimeError(
                f">250 MB upload was not rejected correctly: "
                f"{rejected.status_code} {rejected.get_data(as_text=True)}"
            )

        print(
            f"UPLOAD_LIMIT_TEST=PASS accepted_bytes={source_size} "
            f"rejected_bytes={oversized.stat().st_size} "
            f"rejected_status={rejected.status_code}"
        )
        print("CSRF=ENABLED worker_started=False source_bytes_preserved=True")
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
    finally:
        threading.Thread.start = original_start
        vision_video._jobs.clear()
        temporary.cleanup()


if __name__ == "__main__":
    main()

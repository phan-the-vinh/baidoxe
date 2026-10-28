"""Read-only HTTP smoke checks against the already running local Waitress app."""

import sys
import json
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def get(base_url, path, cookie):
    request = urllib.request.Request(base_url + path, headers={"Cookie": cookie})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.read()


def get_range(base_url, path, cookie):
    request = urllib.request.Request(
        base_url + path,
        headers={"Cookie": cookie, "Range": "bytes=0-31"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, dict(response.headers), response.read()


def main():
    from app import create_app
    from app.extensions import db
    from app.models import User

    app = create_app()
    with app.app_context():
        manager_id = db.session.scalar(select(User.id).where(User.role == "MANAGER").order_by(User.id))
        staff_id = db.session.scalar(select(User.id).where(User.role == "STAFF").order_by(User.id))
        require(manager_id is not None and staff_id is not None, "Thiếu MANAGER hoặc STAFF.")
        serializer = app.session_interface.get_signing_serializer(app)
        require(serializer is not None, "Không tạo được session serializer.")
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        manager_cookie = f"{cookie_name}=" + serializer.dumps({"_user_id": str(manager_id), "_fresh": True})
        staff_cookie = f"{cookie_name}=" + serializer.dumps({"_user_id": str(staff_id), "_fresh": True})
        db.session.rollback()
        db.engine.dispose()

    base_url = "http://127.0.0.1:5000"
    for path in ["/", "/slots", "/entry", "/visits", "/monthly", "/rates", "/statistics", "/vision", "/ai-demo"]:
        status, _ = get(base_url, path, manager_cookie)
        require(status == 200, f"MANAGER {path}: HTTP {status}.")
    status, slots_body = get(base_url, "/api/slots", manager_cookie)
    require(status == 200 and b'"capacity":24' in slots_body and b'"code":"D24"' in slots_body, "API live không trả layout D24.")
    status, _ = get(base_url, "/api/ai-demo/states", manager_cookie)
    require(status == 200, "API /ai-demo live thất bại.")
    status, demo_page = get(base_url, "/ai-demo", manager_cookie)
    require(
        status == 200
        and b"/ai-demo/video?v=" in demo_page
        and b"/vision/video" not in demo_page,
        "Trang /ai-demo không khóa đúng route video cố định.",
    )
    status, states_body = get(base_url, "/api/ai-demo/states", manager_cookie)
    states = json.loads(states_body)
    require(
        status == 200
        and states["total_frames"] == 1623
        and states["duration_seconds"] == 54.1
        and states["video"]["frames"] == 1623,
        "Metadata trạng thái/video demo live không đúng 1.623 frame / 54,1 giây.",
    )
    status, headers, body = get_range(base_url, "/ai-demo/video?v=" + states["artifact_version"], manager_cookie)
    require(
        status == 206
        and len(body) == 32
        and headers.get("Content-Range") == "bytes 0-31/412901056",
        "HTTP Range của video demo đúng không đạt.",
    )
    status, _ = get(base_url, "/slots", staff_cookie)
    require(status == 200, "STAFF không mở được /slots live.")
    try:
        get(base_url, "/rates", staff_cookie)
        raise RuntimeError("STAFF truy cập được /rates.")
    except urllib.error.HTTPError as exc:
        require(exc.code == 403, f"STAFF /rates trả HTTP {exc.code}, không phải 403.")

    print("LIVE_WAITRESS_HTTP=OK")
    print("LIVE_MANAGER_D24_PAGES=OK")
    print("LIVE_STAFF_PERMISSION=OK")
    print("LIVE_AI_DEMO=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

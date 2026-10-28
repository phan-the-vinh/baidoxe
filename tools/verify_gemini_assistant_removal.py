"""Smoke-test removal of the legacy Gemini page without touching ParkingAI."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sqlalchemy import event


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.cli import seed_database
from app.extensions import db
from app.models import User
from app.services import chatbot


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gemini-removal-") as temporary:
        database = Path(temporary) / "smoke.db"
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "gemini-removal-smoke-test-only",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
                "WTF_CSRF_ENABLED": False,
            }
        )
        writes: list[str] = []

        with app.app_context():
            db.create_all()
            seed_database("manager", "test-manager-only")
            staff = User(
                username="employee",
                full_name="Nhân viên kiểm thử",
                role="STAFF",
            )
            staff.set_password("test-employee-only")
            db.session.add(staff)
            db.session.commit()
            engine = db.engine

            def capture(_connection, _cursor, statement, _parameters, _context, _many):
                verb = statement.lstrip().split(None, 1)[0].upper()
                if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP", "CREATE"}:
                    writes.append(statement)

            event.listen(engine, "before_cursor_execute", capture)

        try:
            manager = app.test_client()
            assert manager.post(
                "/login",
                data={"username": "manager", "password": "test-manager-only"},
            ).status_code == 302
            home = manager.get("/")
            assert home.status_code == 200
            assert b'href="/chatbot"' in home.data
            assert b'href="/gemini"' not in home.data
            assert "Trợ lý Gemini".encode("utf-8") not in home.data
            assert manager.get("/gemini").status_code == 404
            assert manager.post("/api/gemini", json={}).status_code == 404
            assert manager.get("/chatbot").status_code == 200
            assert manager.get("/vision").status_code == 200

            original_classifier = chatbot.classify_question_with_qwen
            original_answer = chatbot._qwen_answer
            mock_class = lambda _question: {
                "intent": "CURRENT_OCCUPANCY",
                "parameters": {},
                "confidence": 0.99,
            }
            chatbot.classify_question_with_qwen = mock_class
            chatbot.classify_question_with_gemini = mock_class
            chatbot._qwen_answer = lambda *_args: (None, "simulated-model")
            chatbot._gemini_answer = lambda *_args: (None, "simulated-model")
            try:
                response = manager.post(
                    "/api/chatbot",
                    json={"message": "Hiện còn bao nhiêu chỗ trống?"},
                )
            finally:
                chatbot.classify_question_with_qwen = original_classifier
                chatbot.classify_question_with_gemini = original_classifier
                chatbot._qwen_answer = original_answer
                chatbot._gemini_answer = original_answer
            payload = response.get_json()
            assert response.status_code == 200
            assert payload["ok"] is True
            assert payload["classification_source"] in {"gemini", "qwen"}
            assert payload["backend_query"] == "get_current_occupancy"
            assert payload["data"]["capacity"] == 24

            staff_client = app.test_client()
            assert staff_client.post(
                "/login",
                data={"username": "employee", "password": "test-employee-only"},
            ).status_code == 302
            assert staff_client.get("/chatbot").status_code == 403
            assert staff_client.post(
                "/api/chatbot", json={"message": "Còn chỗ không?"}
            ).status_code == 403
            assert writes == []
        finally:
            with app.app_context():
                event.remove(engine, "before_cursor_execute", capture)
                db.session.remove()
                engine.dispose()

    print(
        "home=200 chatbot=200 vision=200 old_page=404 old_api=404 "
        "gemini_chatbot_path=ok staff_chatbot=403 sql_writes=0"
    )


if __name__ == "__main__":
    main()

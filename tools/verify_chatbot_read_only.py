"""End-to-end chatbot checks against ParkingAI without writing business data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import g
from sqlalchemy import event, func, select, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app import create_app
from app.config import database_url
from app.extensions import db
from app.models import MonthlyPass, Payment, Slot, User, Visit
from app.services import chatbot


QUESTIONS = [
    ("ô nào đang có xe", "OCCUPIED_SLOTS"),
    ("xe đang nằm ở những ô nào vậy?", "OCCUPIED_SLOTS"),
    ("bây giờ còn chỗ đậu không?", "EMPTY_SLOTS"),
    ("những ô nào chưa có xe?", "EMPTY_SLOTS"),
    ("D01 có ai đỗ chưa?", "SLOT_STATUS"),
    ("nay bãi có đông không?", "CURRENT_OCCUPANCY"),
    ("hôm nay người ta vào bãi bao nhiêu lượt?", "TODAY_TRAFFIC"),
    ("nay thu được bao nhiêu tiền?", "REVENUE"),
    ("giờ nào đông nhất vậy?", "PEAK_HOURS"),
    ("vé tháng hiện còn mấy cái dùng được?", "MONTHLY_TICKETS"),
    ("nhân viên muốn ghi xe vào thì làm thế nào?", "SYSTEM_HELP"),
    ("D99 có xe không?", "INVALID_SLOT"),
    ("abc xyz", "UNKNOWN"),
]


def login_as(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session.clear()
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def counts() -> dict:
    return {
        "slots": db.session.scalar(select(func.count()).select_from(Slot)),
        "visits": db.session.scalar(select(func.count()).select_from(Visit)),
        "payments": db.session.scalar(select(func.count()).select_from(Payment)),
        "monthly_passes": db.session.scalar(
            select(func.count()).select_from(MonthlyPass)
        ),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "chatbot-production-read-only-check",
            "SQLALCHEMY_DATABASE_URI": database_url(),
            "WTF_CSRF_ENABLED": False,
        }
    )
    writes: list[str] = []
    results = []
    with app.app_context():
        if db.session.scalar(text("SELECT DB_NAME()")) != "ParkingAI":
            raise RuntimeError("Target database is not ParkingAI")
        before = counts()
        manager_id = db.session.scalar(
            select(User.id).where(User.role == "MANAGER").order_by(User.id)
        )
        staff_id = db.session.scalar(
            select(User.id).where(User.role == "STAFF").order_by(User.id)
        )
        if manager_id is None or staff_id is None:
            raise RuntimeError("Missing MANAGER or STAFF account")

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP", "CREATE"}:
                writes.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            manager = app.test_client()
            login_as(manager, manager_id)
            manager_page = manager.get("/chatbot")
            if manager_page.status_code != 200 or b"data-chatbot" not in manager_page.data:
                raise RuntimeError("Manager chatbot page failed")
            if b"data-chatbot-sample" in manager_page.data or "Hỏi nhanh".encode("utf-8") in manager_page.data:
                raise RuntimeError("Quick-question controls are still present")
            home_page = manager.get("/")
            if b'href="/chatbot"' not in home_page.data:
                raise RuntimeError("Manager chatbot menu is missing")
            if b'href="/gemini"' in home_page.data or "Trợ lý Gemini".encode("utf-8") in home_page.data:
                raise RuntimeError("Removed Gemini assistant is still present in the menu")
            if manager.get("/gemini").status_code != 404:
                raise RuntimeError("Removed Gemini assistant page is still reachable")
            if manager.post("/api/gemini", json={"mode": "report"}).status_code != 404:
                raise RuntimeError("Removed Gemini assistant API is still reachable")

            for question, expected_intent in QUESTIONS:
                response = manager.post("/api/chatbot", json={"message": question})
                payload = response.get_json()
                if expected_intent == "INVALID_SLOT":
                    if response.status_code != 400 or "D01–D24" not in payload.get("error", ""):
                        raise RuntimeError(
                            f"Invalid slot was not rejected: {response.status_code} {payload}"
                        )
                    results.append({
                        "question": question,
                        "intent": "SLOT_STATUS",
                        "answer": payload["error"],
                        "classification_source": "validated_before_query",
                        "backend_query": None,
                    })
                    continue
                if response.status_code != 200 or payload.get("intent") != expected_intent:
                    raise RuntimeError(
                        f"Question failed: {question}: {response.status_code} {payload}"
                    )
                if expected_intent == "UNKNOWN":
                    if payload.get("ok") is not False or payload.get("data") != {}:
                        raise RuntimeError("Unknown question invented data")
                elif payload.get("ok") is not True or not payload.get("answer"):
                    raise RuntimeError(f"Supported intent failed: {expected_intent}")
                results.append(
                    {
                        "question": question,
                        "intent": payload["intent"],
                        "answer": payload["answer"],
                        "ai_used": payload["ai_used"],
                        "answer_source": payload.get("answer_source"),
                        "parameters": payload.get("parameters"),
                        "classification_source": payload.get("classification_source"),
                        "backend_query": payload.get("backend_query"),
                    }
                )

            original_classifier = chatbot.classify_question_with_qwen
            original_answer = chatbot._qwen_answer
            mock_class = lambda *_args: {
                "intent": "SLOT_STATUS",
                "parameters": {"slot_code": "D1"},
                "confidence": 0.97,
            }
            chatbot.classify_question_with_qwen = mock_class
            chatbot.classify_question_with_gemini = mock_class
            chatbot._qwen_answer = lambda *_args: (None, "simulated-model")
            chatbot._gemini_answer = lambda *_args: (None, "simulated-model")
            gemini_primary = manager.post(
                "/api/chatbot", json={"message": "ô đầu tiên sao rồi?"}
            ).get_json()
            if (
                gemini_primary.get("classification_source") not in {"gemini", "qwen"}
                or gemini_primary.get("backend_query") != "get_slot_status"
                or gemini_primary.get("data", {}).get("slot_code") != "D01"
            ):
                raise RuntimeError("AI-primary NLU path failed")

            chatbot.classify_question_with_qwen = lambda *_args: None
            chatbot.classify_question_with_gemini = lambda *_args: None
            chatbot._qwen_answer = lambda *_args: (None, "simulated-failure")
            chatbot._gemini_answer = lambda *_args: (None, "simulated-failure")
            try:
                fallback = manager.post(
                    "/api/chatbot", json={"message": "Hiện còn bao nhiêu chỗ trống?"}
                ).get_json()
            finally:
                chatbot.classify_question_with_qwen = original_classifier
                chatbot.classify_question_with_gemini = original_classifier
                chatbot._qwen_answer = original_answer
                chatbot._gemini_answer = original_answer
            if not fallback.get("answer") or fallback.get("ai_used") is not False:
                raise RuntimeError("Gemini failure fallback failed")

            staff = app.test_client()
            login_as(staff, staff_id)
            g.pop("_login_user", None)
            if staff.get("/chatbot").status_code != 403:
                raise RuntimeError("STAFF can open /chatbot")
            g.pop("_login_user", None)
            if staff.post("/api/chatbot", json={"message": "Còn chỗ không?"}).status_code != 403:
                raise RuntimeError("STAFF can call /api/chatbot")
            g.pop("_login_user", None)
            if b'href="/chatbot"' in staff.get("/").data:
                raise RuntimeError("STAFF can see chatbot menu")

            after = counts()
            if after != before or writes:
                raise RuntimeError(
                    f"Database write detected: before={before}, after={after}, writes={len(writes)}"
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()

    print(
        json.dumps(
            {
                "database": "ParkingAI",
                "counts_before_after": before,
                "tests": results,
                "gemini_failure_fallback": True,
                "gemini_primary_path": True,
                "gemini_assistant_removed": True,
                "quick_questions_removed": True,
                "staff_forbidden": True,
                "sql_writes": len(writes),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

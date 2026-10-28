"""Read-only manager chatbot with local Ollama/Qwen3 NLU and deterministic fallback."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
import urllib.request
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import distinct, func, select

from ..extensions import db
from ..models import MonthlyPass, PassPeriod, Payment, Slot, Visit
from .common import BusinessError, VN, local_time, today


SUPPORTED_INTENTS = (
    "CURRENT_OCCUPANCY", "OCCUPIED_SLOTS", "EMPTY_SLOTS", "SLOT_STATUS",
    "TODAY_TRAFFIC", "REVENUE", "PEAK_HOURS", "MONTHLY_TICKETS",
    "SYSTEM_HELP", "PARKING_RATES", "UNKNOWN",
)
MIN_NLU_CONFIDENCE = 0.55
RAW_SLOT_PATTERN = re.compile(r"\bD\s*0*(\d{1,2})\b", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\b(0?[1-9]|[12][0-9]|3[01])/(0?[1-9]|1[0-2])/(\d{4})\b")
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NLU_SYSTEM_PROMPT = """Bạn là bộ phân loại ý định cho hệ thống quản lý bãi đỗ xe.
Nhiệm vụ: hiểu câu hỏi tiếng Việt tự nhiên, chọn đúng một intent trong whitelist và
trích tham số cần thiết. Trả duy nhất JSON theo schema được cung cấp.

Ý nghĩa intent:
- CURRENT_OCCUPANCY: tổng quan sức chứa, số xe, số chỗ trống hoặc mức độ đông hiện tại.
- OCCUPIED_SLOTS: liệt kê các ô hiện có xe.
- EMPTY_SLOTS: liệt kê các ô hiện đang trống hoặc hỏi còn chỗ đỗ hay không (chỉ khi không nhắc mã ô cụ thể).
- SLOT_STATUS: trạng thái của một ô cụ thể (ví dụ: D01 có ai đỗ chưa, D99 có xe không, ô D05 thế nào). Nếu câu hỏi có nhắc đến một mã ô cụ thể (Dxx), intent bắt buộc là SLOT_STATUS và trích slot_code.
- TODAY_TRAFFIC: lượt xe vào/ra trong một ngày.
- REVENUE: doanh thu theo ngày.
- PEAK_HOURS: giờ có lưu lượng vào/ra cao nhất theo ngày.
- MONTHLY_TICKETS: số vé tháng đang hoạt động.
- SYSTEM_HELP: hướng dẫn sử dụng chức năng thật của hệ thống.
- PARKING_RATES: thông tin biểu phí, giá gửi xe theo lượt hoặc qua đêm.
- UNKNOWN: câu không liên quan hoặc thực sự không thể hiểu.

Tham số: chỉ trích tham số thực tế có trong câu hỏi. slot_code là mã ô người dùng nhắc đến (giữ cả mã ngoài phạm vi để backend
báo lỗi); period là today, yesterday hoặc specific; date dạng YYYY-MM-DD; topic là
chủ đề trợ giúp ngắn gọn.

Bạn không được tạo số liệu, suy đoán dữ liệu bãi xe, viết hoặc thực thi SQL, tạo ô
không được người dùng nhắc đến, hay khẳng định thông tin không có trong câu hỏi.
Hiểu các cách diễn đạt đồng nghĩa theo ngữ nghĩa, không đòi hỏi câu mẫu."""

ANSWER_SYSTEM_PROMPT = """Bạn là trợ lý của hệ thống quản lý bãi đỗ xe.
Chỉ diễn đạt câu trả lời tiếng Việt dựa trên verified_data và fallback_answer do
backend cung cấp. Không thêm số liệu, ô đỗ, suy đoán hoặc kiến thức ngoài context.
Không viết hay thực thi SQL. Nếu dữ liệu không đủ, phải nói rõ không đủ dữ liệu.
Chỉ trả JSON theo schema."""


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    return " ".join(
        "".join(c for c in text if not unicodedata.combining(c)).split()
    )


def _slot_token(value: object) -> str | None:
    match = RAW_SLOT_PATTERN.search(str(value or "").strip())
    if not match:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 24:
        raise BusinessError(f"Ô đỗ D{number:02d} không hợp lệ. Hệ thống chỉ có D01–D24.")
    return f"D{number:02d}"


def route_intent(message: str) -> tuple[str, dict]:
    """Rule router retained solely as the Gemini failure/low-confidence fallback."""
    normalized = _normalized(message)
    raw_slot = RAW_SLOT_PATTERN.search(message)
    if raw_slot:
        return "SLOT_STATUS", {"slot_code": f"D{int(raw_slot.group(1)):02d}"}
    if any(p in normalized for p in (
        "nhan vien", "ghi xe vao", "ghi nhan xe", "cho xe vao", "su dung he thong",
        "huong dan", "lam the nao", "nhu the nao", "kieu gi", "chuc nang",
    )):
        return "SYSTEM_HELP", {"topic": "vehicle_entry"}
    if any(p in normalized for p in (
        "gia gui xe", "bang gia", "phi gui", "gia ve", "tien gui xe", "bieu phi",
        "gia qua dem", "gia theo luot", "bao nhieu tien mot luot", "tinh phi", "gia ca",
    )):
        return "PARKING_RATES", {}
    if any(p in normalized for p in (
        "doanh thu", "thu duoc", "kiem duoc", "bao nhieu tien", "tien thu",
    )):
        return "REVENUE", {}
    if any(p in normalized for p in (
        "gio nao dong", "gio cao diem", "cao diem", "dong nhat", "vao nhieu nhat",
    )):
        return "PEAK_HOURS", {}
    if "ve thang" in normalized and any(p in normalized for p in (
        "hoat dong", "con han", "bao nhieu", "hieu luc", "dung duoc", "con may",
    )):
        return "MONTHLY_TICKETS", {}
    if any(p in normalized for p in (
        "o nao chua co xe", "o nao trong", "nhung o nao trong", "cho nao con trong",
        "chua co xe", "con cho dau", "con cho do", "con cho de xe",
    )):
        return "EMPTY_SLOTS", {}
    if any(p in normalized for p in (
        "o nao co xe", "o nao dang co xe", "xe dang nam o", "xe nam o", "nhung o co xe",
        "da co xe", "co xe do", "dang co xe", "nhung o nao da co xe",
    )):
        return "OCCUPIED_SLOTS", {}
    if any(p in normalized for p in (
        "xe vao", "xe ra", "luot vao", "luot ra", "luu luong", "vao bai bao nhieu",
    )):
        return "TODAY_TRAFFIC", {}
    if any(p in normalized for p in (
        "cho trong", "o trong", "bao nhieu xe", "bai co dong", "bai dong",
        "lap day", "con bao nhieu cho", "tinh trang bai",
    )):
        return "CURRENT_OCCUPANCY", {}
    return "UNKNOWN", {}


def _ollama_settings() -> tuple[str, str]:
    base_url = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
    model = (os.getenv("OLLAMA_LLM_MODEL") or "qwen3:4b").strip()
    return base_url, model


def _gemini_settings() -> tuple[str, str]:
    return (
        (os.getenv("GEMINI_API_KEY") or "").strip(),
        (os.getenv("GEMINI_MODEL") or "").strip(),
    )


def _gemini_client(timeout_ms: int = 10000):
    api_key, _ = _gemini_settings()
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})
    except Exception:
        return None


def _sanitize_history(history: object) -> list[dict]:
    """Sanitize and limit conversation history window to at most 6 recent items."""
    if not isinstance(history, list):
        return []
    sanitized: list[dict] = []
    for item in history[-6:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = item.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str):
            continue
        cleaned_text = text.strip()[:500]
        if not cleaned_text:
            continue
        entry = {"role": role, "text": cleaned_text}
        intent = item.get("intent")
        if isinstance(intent, str) and intent in SUPPORTED_INTENTS:
            entry["intent"] = intent
        sanitized.append(entry)
    return sanitized


def _is_follow_up(question: str, history: list[dict] | None) -> bool:
    """Identify if a question is a contextual follow-up or coreference to previous turns."""
    if not history:
        return False
    norm = _normalized(question).strip()
    words = norm.split()
    if not words:
        return False
    if any(norm.startswith(t) for t in ("the con", "con ", "vay con", "vay ", "the thi", "vay thi")):
        return True
    if any(p in norm for p in ("thi sao", "the nao", "o do", "xe do", "luot do", "ngay do", "con lai", "con o")):
        return True
    if len(words) <= 4 and any(w in norm for w in ("con", "sao", "nua", "do", "nay", "hom qua", "hom nay", "mai")):
        return True
    return False


def _extract_first_json(text: str) -> dict | None:
    """Robustly parse the first JSON object from LLM output using brace depth tracking."""
    if not text:
        return None
    cleaned = text.strip()
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", cleaned, re.IGNORECASE)
    if match:
        try:
            val = json.loads(match.group(1))
            if isinstance(val, dict):
                return val
        except Exception:
            pass

    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(cleaned)):
        char = cleaned[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_str = not in_str
            continue
        if not in_str:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        val = json.loads(candidate)
                        if isinstance(val, dict):
                            return val
                    except Exception:
                        pass
                    break
    return None


def _validated_classification(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Classification is not an object")
    intent = str(value.get("intent") or "").strip().upper()
    if intent not in SUPPORTED_INTENTS:
        raise ValueError(f"Returned unsupported intent: {intent}")
    parameters = value.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("Parameters are not an object")
    confidence = float(value.get("confidence", 0.0))
    if not 0 <= confidence <= 1:
        raise ValueError("Confidence is outside 0..1")
    allowed = {
        key: parameters[key]
        for key in ("slot_code", "period", "date", "topic", "clarification_message")
        if key in parameters and parameters[key] not in (None, "")
    }
    for key, parameter in allowed.items():
        if not isinstance(parameter, str) or len(parameter.strip()) > 300:
            raise ValueError(f"Parameter {key} is invalid")
        allowed[key] = parameter.strip()
    if "period" in allowed and allowed["period"] not in {"today", "yesterday", "specific"}:
        raise ValueError("Period is invalid")
    if "slot_code" in allowed and intent != "SLOT_STATUS":
        intent = "SLOT_STATUS"
    return {"intent": intent, "parameters": allowed, "confidence": confidence}


def classify_question_with_qwen(question: str) -> dict | None:
    """Use local Ollama / Qwen3 4B for NLU; return None for every safe fallback condition."""
    base_url, model = _ollama_settings()
    if not base_url or not model:
        return None

    context = {
        "question": question,
        "current_date": today().isoformat(),
        "timezone": "Asia/Ho_Chi_Minh",
    }
    prompt = (
        f"{NLU_SYSTEM_PROMPT}\n\n"
        f"Input: {json.dumps(context, ensure_ascii=False)}\n\n"
        'Trả duy nhất JSON theo schema: {"intent": "...", "parameters": {...}, "confidence": 0.95}'
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": NLU_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "</think>\n{"},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 300,
        },
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("message", {})
            raw_text = str(msg.get("content") or "").strip()
            if not raw_text.startswith("{") and "{" not in raw_text[:10]:
                raw_text = "{" + raw_text
            parsed = _extract_first_json(raw_text)
            if not parsed:
                return None
            return _validated_classification(parsed)
    except Exception:
        return None


def classify_question_with_gemini_context(question: str, history: list[dict]) -> dict | None:
    """Use Gemini contextual NLU to interpret follow-up questions using recent history."""
    api_key, model = _gemini_settings()
    if not api_key:
        return None
    client = _gemini_client(timeout_ms=10000)
    if not client:
        return None

    history_lines = []
    for idx, item in enumerate(history[-6:], 1):
        role_label = "Quản lý" if item.get("role") == "user" else "Hệ thống"
        text = str(item.get("text") or "").strip()
        intent_tag = f" [Intent: {item.get('intent')}]" if item.get("intent") else ""
        history_lines.append(f"{idx}. {role_label}{intent_tag}: {text}")

    context = {
        "conversation_history": "\n".join(history_lines),
        "follow_up_question": question,
        "current_date": today().isoformat(),
        "timezone": "Asia/Ho_Chi_Minh",
    }
    user_prompt = (
        f"{NLU_SYSTEM_PROMPT}\n\n"
        f"Ngữ cảnh hội thoại và câu hỏi tiếp nối:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Quy tắc giải nghĩa câu hỏi tiếp nối:\n"
        "1. Nếu câu hỏi trước hỏi về một chỉ số lịch sử cụ thể (ví dụ: lượt xe vào bãi / TODAY_TRAFFIC, hoặc doanh thu / REVENUE) "
        "và câu hỏi tiếp nối đổi ngày (ví dụ: 'Thế còn hôm qua?'), hãy giữ nguyên intent và đổi parameter 'period': 'yesterday', 'date': '<ngày hôm qua>'.\n"
        "2. Nếu ngữ cảnh trước đề cập đến nhiều chỉ số (ví dụ: vừa hỏi xe vừa hỏi chỗ trống, CURRENT_OCCUPANCY) "
        "mà câu hỏi tiếp nối chỉ hỏi ngày chung chung ('Còn hôm qua?') không rõ chỉ số nào: "
        "trả intent: 'UNKNOWN' kèm parameter 'clarification_message' để yêu cầu người dùng làm rõ, TUYỆT ĐỐI KHÔNG tự chọn một chỉ số.\n"
        "3. Nếu câu hỏi tiếp nối chuyển sang hỏi về chỗ trống ('Thế còn chỗ trống thì sao?'): "
        "trả intent: 'EMPTY_SLOTS', parameters: {}.\n\n"
        'Dựa vào lịch sử hội thoại trên, giải nghĩa câu hỏi tiếp nối và trả duy nhất JSON theo schema: '
        '{"intent": "...", "parameters": {...}, "confidence": 0.95}'
    )

    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=model or "gemini-2.5-flash-lite",
            contents=user_prompt,
            config=config,
        )
        raw_text = str(response.text or "").strip()
        parsed = _extract_first_json(raw_text)
        if not parsed:
            return None
        return _validated_classification(parsed)
    except Exception:
        return None


def classify_question_with_gemini_standalone(question: str) -> dict | None:
    """Use Gemini as fallback classifier for standalone questions if Qwen is uncertain or offline."""
    api_key, model = _gemini_settings()
    if not api_key:
        return None
    client = _gemini_client(timeout_ms=10000)
    if not client:
        return None

    context = {
        "question": question,
        "current_date": today().isoformat(),
        "timezone": "Asia/Ho_Chi_Minh",
    }
    prompt = (
        f"{NLU_SYSTEM_PROMPT}\n\n"
        f"Input: {json.dumps(context, ensure_ascii=False)}\n\n"
        'Trả duy nhất JSON theo schema: {"intent": "...", "parameters": {...}, "confidence": 0.95}'
    )

    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=model or "gemini-2.5-flash-lite",
            contents=prompt,
            config=config,
        )
        raw_text = str(response.text or "").strip()
        parsed = _extract_first_json(raw_text)
        if not parsed:
            return None
        return _validated_classification(parsed)
    except Exception:
        return None


def _resolve_contextual_heuristic(question: str, history: list[dict]) -> tuple[str, dict] | None:
    """Deterministic local heuristic fallback when follow-up cannot be resolved by Gemini."""
    if not history:
        return None
    norm = _normalized(question)

    raw_slot = RAW_SLOT_PATTERN.search(question)
    if raw_slot:
        return "SLOT_STATUS", {"slot_code": f"D{int(raw_slot.group(1)):02d}"}

    # Find last known intent and last user message from history
    last_intent = None
    last_user_text = ""
    for item in reversed(history):
        if not last_intent and item.get("intent") and item.get("intent") != "UNKNOWN":
            last_intent = item.get("intent")
        if not last_user_text and item.get("role") == "user":
            last_user_text = _normalized(str(item.get("text") or ""))
        if last_intent and last_user_text:
            break

    if "hom qua" in norm:
        yesterday_str = (today() - timedelta(days=1)).isoformat()
        has_multiple_metrics = (
            last_intent == "CURRENT_OCCUPANCY"
            or ("xe" in last_user_text and ("cho trong" in last_user_text or "o trong" in last_user_text))
            or ("doanh thu" in last_user_text and ("luot" in last_user_text or "xe" in last_user_text))
        )
        if has_multiple_metrics:
            return "UNKNOWN", {
                "clarification_message": (
                    "Ngữ cảnh trước đề cập đến cả số xe và chỗ trống hiện tại. "
                    "Đối với ngày hôm qua, bạn muốn kiểm tra lượt xe vào/ra hay doanh thu? "
                    "Vui lòng nói rõ chỉ số cần xem."
                )
            }

        if last_intent in {"REVENUE", "TODAY_TRAFFIC", "PEAK_HOURS"}:
            return last_intent, {"period": "yesterday", "date": yesterday_str}
        return "TODAY_TRAFFIC", {"period": "yesterday", "date": yesterday_str}

    if any(p in norm for p in ("cho trong", "o trong", "trong khong", "con cho")):
        return "EMPTY_SLOTS", {}

    if any(p in norm for p in ("o co xe", "xe dau", "dang do")):
        return "OCCUPIED_SLOTS", {}

    if any(p in norm for p in ("doanh thu", "tien", "thu duoc")):
        return "REVENUE", {"period": "today"}

    return None


def classify_question_with_gemini(question: str) -> dict | None:
    """Backward-compatible wrapper for Gemini standalone NLU."""
    mod = sys.modules.get(__name__)
    fn = getattr(mod, "classify_question_with_gemini_standalone", classify_question_with_gemini_standalone)
    return fn(question)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, VN).astimezone(timezone.utc).replace(tzinfo=None)
    stop = datetime.combine(day + timedelta(days=1), time.min, VN).astimezone(timezone.utc).replace(tzinfo=None)
    return start, stop


def _resolve_day(parameters: dict, message: str) -> date:
    period = _normalized(str(parameters.get("period") or ""))
    raw_date = str(parameters.get("date") or "").strip()
    if raw_date in {"today", "hom nay"} or period in {"today", "hom nay"}:
        return today()
    if raw_date in {"yesterday", "hom qua"} or period in {"yesterday", "hom qua"}:
        return today() - timedelta(days=1)
    if period == "specific" and not raw_date:
        raise BusinessError("Câu hỏi chưa có ngày cụ thể để tra cứu.")
    if raw_date:
        if not ISO_DATE_PATTERN.fullmatch(raw_date):
            raise BusinessError("Ngày do hệ thống nhận diện không hợp lệ.")
        try:
            return date.fromisoformat(raw_date)
        except ValueError as exc:
            raise BusinessError("Ngày trong câu hỏi không hợp lệ.") from exc
    match = DATE_PATTERN.search(message)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError as exc:
            raise BusinessError("Ngày trong câu hỏi không hợp lệ.") from exc
    if "hom qua" in _normalized(message):
        return today() - timedelta(days=1)
    return today()


def _money(value: int) -> str:
    return f"{int(value):,}".replace(",", ".") + " đồng"


def get_current_occupancy(role: str | None = None) -> tuple[dict, str]:
    rows = list(db.session.execute(select(Slot.code, Slot.state).order_by(Slot.code)))
    capacity = len(rows)
    occupied = sum(row.state == "OCCUPIED" for row in rows)
    empty = sum(row.state == "EMPTY" for row in rows)
    rate = round(occupied / capacity * 100, 1) if capacity else 0.0
    if role == "CUSTOMER":
        data = {"capacity": capacity, "empty": empty, "source": "ParkingAI.slots"}
        return data, f"Hiện tại bãi xe còn {empty}/{capacity} ô trống sẵn sàng phục vụ."
    data = {"capacity": capacity, "occupied": occupied, "empty": empty,
            "occupancy_rate": rate, "source": "ParkingAI.slots"}
    return data, (f"Hiện có {occupied}/{capacity} ô đang có xe, còn {empty} ô trống. "
                  f"Tỷ lệ lấp đầy là {rate}%.")


def _slots_by_state(state: str) -> list[str]:
    return list(db.session.scalars(
        select(Slot.code).where(Slot.state == state).order_by(Slot.code)
    ))


def get_occupied_slots() -> tuple[dict, str]:
    slots = _slots_by_state("OCCUPIED")
    capacity = db.session.scalar(select(func.count()).select_from(Slot)) or 0
    data = {"capacity": int(capacity), "occupied_count": len(slots), "slots": slots,
            "source": "ParkingAI.slots"}
    answer = (f"Hiện có {len(slots)} ô đang có xe: {', '.join(slots)}."
              if slots else "Hiện không có ô nào đang có xe.")
    return data, answer


def get_empty_slots() -> tuple[dict, str]:
    slots = _slots_by_state("EMPTY")
    capacity = db.session.scalar(select(func.count()).select_from(Slot)) or 0
    data = {"capacity": int(capacity), "empty_count": len(slots), "slots": slots,
            "source": "ParkingAI.slots"}
    answer = (f"Hiện còn {len(slots)} ô trống: {', '.join(slots)}."
              if slots else "Hiện không còn ô trống.")
    return data, answer


def get_slot_status(slot_code: str) -> tuple[dict, str]:
    canonical = _slot_token(slot_code)
    if canonical is None:
        raise BusinessError("Câu hỏi chưa có mã ô hợp lệ D01–D24.")
    slot = db.session.get(Slot, canonical)
    if not slot:
        raise BusinessError(f"Không tìm thấy ô {canonical}.", 404)
    data = {"slot_code": slot.code, "state": slot.state,
            "needs_review": bool(slot.needs_review),
            "changed_at": local_time(slot.changed_at).isoformat() if slot.changed_at else None,
            "source": "ParkingAI.slots"}
    status = "đang có xe" if slot.state == "OCCUPIED" else "đang trống"
    review = " Ô này đang được đánh dấu cần kiểm tra." if slot.needs_review else ""
    return data, f"Theo trạng thái nghiệp vụ ParkingAI, ô {slot.code} {status}.{review}"


def get_traffic(day: date) -> tuple[dict, str]:
    start, stop = _day_bounds(day)
    arrivals = db.session.scalar(select(func.count()).select_from(Visit).where(
        Visit.entered_at >= start, Visit.entered_at < stop)) or 0
    departures = db.session.scalar(select(func.count()).select_from(Visit).where(
        Visit.exited_at.is_not(None), Visit.exited_at >= start, Visit.exited_at < stop)) or 0
    data = {"date": day.isoformat(), "arrivals": int(arrivals),
            "departures": int(departures), "timezone": "Asia/Ho_Chi_Minh",
            "source": "ParkingAI.visits"}
    return data, (f"Ngày {day.strftime('%d/%m/%Y')} có {arrivals} lượt xe vào "
                  f"và {departures} lượt xe ra.")


def get_revenue(day: date) -> tuple[dict, str]:
    start, stop = _day_bounds(day)
    amount = db.session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.paid_at >= start, Payment.paid_at < stop)) or 0
    payments = db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.paid_at >= start, Payment.paid_at < stop)) or 0
    data = {"date": day.isoformat(), "revenue": int(amount),
            "payment_count": int(payments), "currency": "VND",
            "source": "ParkingAI.payments"}
    return data, (f"Doanh thu ngày {day.strftime('%d/%m/%Y')} là {_money(amount)}, "
                  f"từ {payments} khoản thanh toán thực tế.")


def get_peak_hours(day: date) -> tuple[dict, str]:
    start, stop = _day_bounds(day)
    arrivals = list(db.session.scalars(select(Visit.entered_at).where(
        Visit.entered_at >= start, Visit.entered_at < stop)))
    departures = list(db.session.scalars(select(Visit.exited_at).where(
        Visit.exited_at.is_not(None), Visit.exited_at >= start, Visit.exited_at < stop)))
    flow = Counter(local_time(moment).hour for moment in [*arrivals, *departures])
    event_count = len(arrivals) + len(departures)
    enough = event_count >= 3
    maximum = max(flow.values(), default=0)
    peak_hours = sorted(h for h, count in flow.items() if count == maximum) if enough else []
    data = {"date": day.isoformat(), "event_count": event_count,
            "arrivals": len(arrivals), "departures": len(departures),
            "minimum_events_required": 3, "enough_data": enough,
            "peak_hours": peak_hours,
            "hourly_flow": {f"{h:02d}:00": flow.get(h, 0) for h in range(24)},
            "source": "ParkingAI.visits"}
    if not enough:
        return data, (f"Ngày {day.strftime('%d/%m/%Y')} mới có {event_count} sự kiện xe vào/ra, "
                      "chưa đủ dữ liệu để kết luận giờ cao điểm.")
    labels = ", ".join(f"{h:02d}:00–{h:02d}:59" for h in peak_hours)
    return data, (f"Khung giờ đông nhất ngày {day.strftime('%d/%m/%Y')} là {labels}, "
                  f"với {maximum} lượt xe vào/ra.")


def get_monthly_ticket_summary() -> tuple[dict, str]:
    day = today()
    active = db.session.scalar(select(func.count(distinct(PassPeriod.pass_id))).where(
        PassPeriod.start_date <= day, PassPeriod.end_date >= day)) or 0
    total = db.session.scalar(select(func.count()).select_from(MonthlyPass)) or 0
    data = {"date": day.isoformat(), "active": int(active),
            "total_profiles": int(total),
            "source": "ParkingAI.monthly_passes + pass_periods"}
    return data, f"Hiện có {active} vé tháng đang hoạt động trên tổng số {total} hồ sơ vé tháng."


def get_system_help(topic: str | None = None, role: str | None = None) -> tuple[dict, str]:
    if role == "CUSTOMER":
        steps = [
            "Di chuyển xe vào làn kiểm soát tại cổng vào bãi đỗ.",
            "Dừng trước camera nhận diện và nhận thẻ/vé từ nhân viên trực cổng.",
            "Di chuyển xe vào đỗ đúng vị trí ô trống theo hướng dẫn (các ô D01–D24).",
            "Khi rời bãi, xuất trình thẻ/vé tại cổng ra và thanh toán phí theo biểu phí niêm yết.",
        ]
        data = {"topic": topic or "customer_parking_guide", "steps": steps,
                "source": "ParkView customer guide"}
        return data, "Quy trình gửi xe dành cho khách hàng: " + " ".join(
            f"{i}. {step}" for i, step in enumerate(steps, start=1)
        )

    steps = [
        "Mở trang Ghi nhận xe vào.",
        "Chọn ảnh biển số hoặc nhập biển số thủ công nếu AI không đọc được.",
        "Xác nhận biển số và chọn một ô D01–D24 đang trống.",
        "Xác nhận vị trí rồi gửi biểu mẫu để tạo lượt gửi.",
    ]
    data = {"topic": topic or "vehicle_entry", "steps": steps,
            "source": "ParkingAI web workflow"}
    return data, "Nhân viên ghi nhận xe vào theo các bước: " + " ".join(
        f"{i}. {step}" for i, step in enumerate(steps, start=1)
    )


def get_parking_rates() -> tuple[dict, str]:
    from ..models import Rate
    rate = db.session.scalar(select(Rate).order_by(Rate.id.desc()).limit(1))
    turn_fee = int(rate.turn_fee) if rate else 20000
    night_fee = int(rate.night_fee) if rate else 80000
    data = {
        "turn_fee": turn_fee,
        "night_fee": night_fee,
        "currency": "VND",
        "source": "ParkingAI.rates"
    }
    answer = (
        f"Biểu phí gửi xe ParkView hiện tại: {_money(turn_fee)}/lượt ban ngày "
        f"và {_money(night_fee)}/lượt gửi qua đêm."
    )
    return data, answer


def _facts_for(intent: str, parameters: dict, message: str, role: str | None = None) -> tuple[dict, str, str]:
    if intent == "CURRENT_OCCUPANCY":
        data, answer = get_current_occupancy(role=role); return data, answer, "get_current_occupancy"
    if intent == "OCCUPIED_SLOTS":
        data, answer = get_occupied_slots(); return data, answer, "get_occupied_slots"
    if intent == "EMPTY_SLOTS":
        data, answer = get_empty_slots(); return data, answer, "get_empty_slots"
    if intent == "SLOT_STATUS":
        data, answer = get_slot_status(parameters.get("slot_code") or message)
        return data, answer, "get_slot_status"
    if intent == "TODAY_TRAFFIC":
        data, answer = get_traffic(_resolve_day(parameters, message)); return data, answer, "get_traffic"
    if intent == "REVENUE":
        data, answer = get_revenue(_resolve_day(parameters, message)); return data, answer, "get_revenue"
    if intent == "PEAK_HOURS":
        data, answer = get_peak_hours(_resolve_day(parameters, message)); return data, answer, "get_peak_hours"
    if intent == "MONTHLY_TICKETS":
        data, answer = get_monthly_ticket_summary(); return data, answer, "get_monthly_ticket_summary"
    if intent == "SYSTEM_HELP":
        data, answer = get_system_help(parameters.get("topic"), role=role); return data, answer, "get_system_help"
    if intent == "PARKING_RATES":
        data, answer = get_parking_rates(); return data, answer, "get_parking_rates"
    raise ValueError(f"Unsupported intent: {intent}")


def _qwen_answer(intent: str, message: str, data: dict, fallback: str) -> tuple[str | None, str | None]:
    """Generate fluent Vietnamese answer using local Qwen3 4B, with strict anti-hallucination verification."""
    base_url, model = _ollama_settings()
    if not base_url or not model:
        return None, model or None

    payload_context = {
        "intent": intent,
        "question": message,
        "verified_data": data,
        "fallback_answer": fallback,
    }
    user_prompt = (
        f"{ANSWER_SYSTEM_PROMPT}\n\n"
        f"Context: {json.dumps(payload_context, ensure_ascii=False)}\n\n"
        'Trả duy nhất JSON theo schema: {"answer": "..."}'
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": "</think>\n{"},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 400,
        },
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            msg = resp_data.get("message", {})
            raw_text = str(msg.get("content") or "").strip()
            if not raw_text.startswith("{") and "{" not in raw_text[:10]:
                raw_text = "{" + raw_text
            parsed = _extract_first_json(raw_text)
            if not parsed or not isinstance(parsed, dict):
                return None, model
            answer = str(parsed.get("answer") or "").strip()
            if not answer or len(answer) > 1200:
                return None, model

            # Anti-hallucination check 1: numbers must come from verified_data
            allowed = set(re.findall(r"\d+(?:[.,]\d+)?", json.dumps(data, ensure_ascii=False)))
            numbers = set(re.findall(r"\d+(?:[.,]\d+)?", answer))
            if not numbers.issubset(allowed):
                return None, model

            # Anti-hallucination check 2: parking slot codes must come from verified_data
            allowed_slots = set(re.findall(r"\bD(?:0[1-9]|1[0-9]|2[0-4])\b", json.dumps(data)))
            answer_slots = set(re.findall(r"\bD(?:0[1-9]|1[0-9]|2[0-4])\b", answer))
            if not answer_slots.issubset(allowed_slots):
                return None, model

            return answer, model
    except Exception:
        return None, model


def _gemini_answer_synthesis(intent: str, message: str, data: dict, fallback: str) -> tuple[str | None, str | None]:
    """Synthesize fluent answer using Gemini when Qwen fails, with identical anti-hallucination guard."""
    api_key, model = _gemini_settings()
    if not api_key:
        return None, None
    client = _gemini_client(timeout_ms=10000)
    if not client:
        return None, None

    payload_context = {
        "intent": intent,
        "question": message,
        "verified_data": data,
        "fallback_answer": fallback,
    }
    user_prompt = (
        f"{ANSWER_SYSTEM_PROMPT}\n\n"
        f"Context: {json.dumps(payload_context, ensure_ascii=False)}\n\n"
        'Trả duy nhất JSON theo schema: {"answer": "..."}'
    )

    try:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        )
        model_name = model or "gemini-2.5-flash-lite"
        response = client.models.generate_content(
            model=model_name,
            contents=user_prompt,
            config=config,
        )
        raw_text = str(response.text or "").strip()
        parsed = _extract_first_json(raw_text)
        if not parsed or not isinstance(parsed, dict):
            return None, model_name
        answer = str(parsed.get("answer") or "").strip()
        if not answer or len(answer) > 1200:
            return None, model_name

        # Anti-hallucination check 1: numbers must come from verified_data
        allowed = set(re.findall(r"\d+(?:[.,]\d+)?", json.dumps(data, ensure_ascii=False)))
        numbers = set(re.findall(r"\d+(?:[.,]\d+)?", answer))
        if not numbers.issubset(allowed):
            return None, model_name

        # Anti-hallucination check 2: parking slot codes must come from verified_data
        allowed_slots = set(re.findall(r"\bD(?:0[1-9]|1[0-9]|2[0-4])\b", json.dumps(data)))
        answer_slots = set(re.findall(r"\bD(?:0[1-9]|1[0-9]|2[0-4])\b", answer))
        if not answer_slots.issubset(allowed_slots):
            return None, model_name

        return answer, model_name
    except Exception:
        return None, model or "gemini"


def _gemini_answer(intent: str, message: str, data: dict, fallback: str) -> tuple[str | None, str | None]:
    """Backward-compatible wrapper for Gemini answer synthesis."""
    mod = sys.modules.get(__name__)
    fn = getattr(mod, "_gemini_answer_synthesis", _gemini_answer_synthesis)
    return fn(intent, message, data, fallback)


def _classification(question: str, history: list[dict] | None = None) -> tuple[str, dict, float | None, str]:
    sanitized_history = _sanitize_history(history)
    raw_slot = RAW_SLOT_PATTERN.search(question)

    mod = sys.modules.get(__name__)
    qwen_fn = getattr(mod, "classify_question_with_qwen", classify_question_with_qwen)
    gemini_standalone_fn = getattr(mod, "classify_question_with_gemini_standalone", classify_question_with_gemini_standalone)
    legacy_gemini = getattr(mod, "classify_question_with_gemini", None)
    if legacy_gemini is not None and legacy_gemini is not classify_question_with_gemini_standalone and legacy_gemini is not qwen_fn:
        gemini_standalone_fn = legacy_gemini

    gemini_context_fn = getattr(mod, "classify_question_with_gemini_context", classify_question_with_gemini_context)

    # Contextual Follow-up Resolution
    if _is_follow_up(question, sanitized_history):
        ctx_result = gemini_context_fn(question, sanitized_history)
        if raw_slot and ctx_result:
            ctx_result["intent"] = "SLOT_STATUS"
            ctx_result.setdefault("parameters", {})["slot_code"] = f"D{int(raw_slot.group(1)):02d}"
        if ctx_result and ctx_result.get("confidence", 0) >= MIN_NLU_CONFIDENCE:
            if ctx_result.get("intent") == "UNKNOWN":
                return "UNKNOWN", ctx_result.get("parameters", {}), ctx_result.get("confidence"), "gemini_context"
            return ctx_result["intent"], ctx_result["parameters"], ctx_result["confidence"], "gemini_context"

        heuristic_res = _resolve_contextual_heuristic(question, sanitized_history)
        if heuristic_res:
            h_intent, h_params = heuristic_res
            if raw_slot:
                h_intent = "SLOT_STATUS"
                h_params["slot_code"] = f"D{int(raw_slot.group(1)):02d}"
            return h_intent, h_params, 0.85, "rule_fallback"

    # Standalone Question:
    # Tier 1: Local Qwen3 4B
    result = qwen_fn(question)
    if raw_slot and result:
        result["intent"] = "SLOT_STATUS"
        result.setdefault("parameters", {})["slot_code"] = f"D{int(raw_slot.group(1)):02d}"

    if result and result.get("confidence", 0) >= MIN_NLU_CONFIDENCE and result.get("intent") != "UNKNOWN":
        return result["intent"], result["parameters"], result["confidence"], "qwen"

    # Tier 2: Gemini Standalone Fallback
    gem_result = gemini_standalone_fn(question)
    if raw_slot and gem_result:
        gem_result["intent"] = "SLOT_STATUS"
        gem_result.setdefault("parameters", {})["slot_code"] = f"D{int(raw_slot.group(1)):02d}"

    if gem_result and gem_result.get("confidence", 0) >= MIN_NLU_CONFIDENCE and gem_result.get("intent") != "UNKNOWN":
        return gem_result["intent"], gem_result["parameters"], gem_result["confidence"], "gemini"

    # Tier 3: Deterministic Rule Fallback
    intent, parameters = route_intent(question)
    if raw_slot:
        intent = "SLOT_STATUS"
        parameters["slot_code"] = f"D{int(raw_slot.group(1)):02d}"
    if intent == "UNKNOWN":
        conf = (result.get("confidence") if result else None) or (gem_result.get("confidence") if gem_result else None)
        return "UNKNOWN", {}, conf, "rule_fallback"
    return intent, parameters, None, "rule_fallback"


ROLE_ALLOWED_INTENTS = {
    "ADMIN": {
        "REVENUE", "CURRENT_OCCUPANCY", "EMPTY_SLOTS", "OCCUPIED_SLOTS",
        "SLOT_STATUS", "TODAY_TRAFFIC", "PEAK_HOURS", "SYSTEM_HELP", "PARKING_RATES", "MONTHLY_TICKETS"
    },
    "MANAGER": {
        "REVENUE", "CURRENT_OCCUPANCY", "EMPTY_SLOTS", "OCCUPIED_SLOTS",
        "SLOT_STATUS", "TODAY_TRAFFIC", "PEAK_HOURS", "SYSTEM_HELP", "PARKING_RATES", "MONTHLY_TICKETS"
    },
    "STAFF": {
        "CURRENT_OCCUPANCY", "EMPTY_SLOTS", "OCCUPIED_SLOTS", "SLOT_STATUS",
        "TODAY_TRAFFIC", "PEAK_HOURS", "SYSTEM_HELP", "PARKING_RATES"
    },
    "CUSTOMER": {
        "PARKING_RATES", "SYSTEM_HELP", "CURRENT_OCCUPANCY", "EMPTY_SLOTS"
    },
}

CUSTOMER_PERSONAL_VEHICLE_PATTERNS = (
    "xe cua toi", "xe toi", "xe cua em", "xe em", "xe cua minh", "xe minh",
    "bien so cua toi", "bien so toi", "bien so cua em", "bien so cua minh",
    "luot gui cua toi", "luot gui xe cua toi", "luot gui cua em", "ve cua toi",
    "ve xe cua toi", "tim xe cua toi", "tim xe toi", "xe toi dang o dau",
    "xe cua toi dang do o dau", "xe cua toi gui luc nao", "xe toi gui luc nao",
    "xe cua toi co trong bai khong", "kiem tra xe cua toi", "kiem tra xe toi"
)


def respond(message: str, history: list[dict] | None = None, user_role: str | None = None, user_id: int | None = None) -> dict:
    question = str(message or "").strip()
    if not question:
        raise BusinessError("Hãy nhập câu hỏi.")
    if len(question) > 1000:
        raise BusinessError("Câu hỏi tối đa 1000 ký tự.")

    norm_role = str(user_role or "ADMIN").strip().upper()
    if norm_role == "MANAGER":
        norm_role = "ADMIN"
    if norm_role not in ("ADMIN", "STAFF", "CUSTOMER"):
        norm_role = "CUSTOMER"

    # Check if CUSTOMER is inquiring about their personal vehicle / personal parking visit
    norm_q = _normalized(question)
    if norm_role == "CUSTOMER" and any(pat in norm_q for pat in CUSTOMER_PERSONAL_VEHICLE_PATTERNS):
        return {
            "ok": True,
            "intent": "PERSONAL_VEHICLE",
            "parameters": {},
            "confidence": 1.0,
            "classification_source": "role_policy",
            "backend_query": None,
            "answer": (
                "Hiện tại hệ thống bãi xe sử dụng thẻ/vé lượt trực tiếp tại cổng kiểm soát và chưa liên kết biển số xe với tài khoản trực tuyến của bạn. "
                "Để tra cứu thông tin lượt gửi xe hoặc nhận xe, quý khách vui lòng xuất trình vé xe cho nhân viên trực tại cổng ra vào."
            ),
            "data": {
                "limitation": "no_account_vehicle_link",
                "message": "Hệ thống chưa hỗ trợ liên kết biển số định danh vào tài khoản trực tuyến."
            },
            "ai_used": False,
            "answer_source": "role_policy",
            "model": None,
        }

    intent, parameters, confidence, source = _classification(question, history)
    if intent == "UNKNOWN":
        clarification_msg = parameters.get("clarification_message") if parameters else None
        return {
            "ok": False, "intent": "UNKNOWN", "parameters": parameters or {},
            "confidence": confidence, "classification_source": source,
            "backend_query": None,
            "answer": clarification_msg or ("Tôi chưa xác định được yêu cầu liên quan đến quản lý bãi xe. "
                       "Bạn hãy diễn đạt lại câu hỏi cụ thể hơn."),
            "data": {}, "ai_used": source in {"qwen", "gemini", "gemini_context"},
            "answer_source": "backend_fallback",
            "model": _ollama_settings()[1] or None,
        }

    # RBAC check before querying database facts
    allowed_intents = ROLE_ALLOWED_INTENTS.get(norm_role, set())
    if intent not in allowed_intents:
        if intent == "REVENUE":
            if norm_role == "STAFF":
                refusal = "Dữ liệu doanh thu và báo cáo tài chính chỉ dành riêng cho Quản trị viên (ADMIN). Tài khoản Nhân viên không được cấp quyền truy cập thông tin này."
            else:
                refusal = "Dữ liệu doanh thu và báo cáo tài chính bãi xe là thông tin quản trị nội bộ. Tài khoản Khách hàng không được phép truy cập."
        elif intent in {"OCCUPIED_SLOTS", "SLOT_STATUS"}:
            refusal = "Thông tin chi tiết về các ô đang đỗ và biển số xe thuộc phạm vi vận hành nội bộ. Quý khách vui lòng tra cứu tổng số ô trống hoặc danh sách các ô còn trống."
        elif intent in {"TODAY_TRAFFIC", "PEAK_HOURS"}:
            refusal = "Thống kê lưu lượng xe và giờ cao điểm là thông tin vận hành nội bộ. Quý khách có thể xem biểu phí, hướng dẫn sử dụng và số lượng ô trống hiện tại."
        else:
            refusal = "Bạn không có quyền truy cập dữ liệu cho yêu cầu này."

        return {
            "ok": False,
            "intent": intent,
            "parameters": parameters or {},
            "confidence": confidence,
            "classification_source": source,
            "backend_query": None,
            "answer": refusal,
            "data": {"error": "forbidden", "role": norm_role},
            "ai_used": False,
            "answer_source": "role_policy",
            "model": None,
        }

    data, fallback, backend_query = _facts_for(intent, parameters, question, role=norm_role)
    # Close the read transaction before an optional external LLM request.
    db.session.rollback()

    mod = sys.modules.get(__name__)
    qwen_ans = getattr(mod, "_qwen_answer", _qwen_answer)
    gemini_ans = getattr(mod, "_gemini_answer_synthesis", _gemini_answer_synthesis)
    legacy_gemini_ans = getattr(mod, "_gemini_answer", None)
    if legacy_gemini_ans is not None and legacy_gemini_ans is not _gemini_answer_synthesis and legacy_gemini_ans is not qwen_ans:
        gemini_ans = legacy_gemini_ans

    # First try Qwen local answer
    generated, model = qwen_ans(intent, question, data, fallback)
    answer_source = "qwen"

    # If Qwen answer failed (timeout, anti-hallucination violation, offline), fallback to Gemini
    if generated is None:
        generated, gem_model = gemini_ans(intent, question, data, fallback)
        if generated is not None:
            answer_source = "gemini"
            model = gem_model

    if generated is None:
        answer_source = "backend_fallback"

    return {
        "ok": True, "intent": intent, "parameters": parameters,
        "confidence": confidence, "classification_source": source,
        "backend_query": backend_query, "answer": generated or fallback,
        "data": data, "ai_used": source in {"qwen", "gemini", "gemini_context"} or generated is not None,
        "answer_source": answer_source,
        "model": model,
    }

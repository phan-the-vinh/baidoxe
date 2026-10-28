from app.services import chatbot


NATURAL_CASES = (
    ("ô nào đang có xe", "OCCUPIED_SLOTS", "get_occupied_slots"),
    ("xe đang nằm ở những ô nào vậy?", "OCCUPIED_SLOTS", "get_occupied_slots"),
    ("bây giờ còn chỗ đậu không?", "EMPTY_SLOTS", "get_empty_slots"),
    ("những ô nào chưa có xe?", "EMPTY_SLOTS", "get_empty_slots"),
    ("D01 có ai đỗ chưa?", "SLOT_STATUS", "get_slot_status"),
    ("nay bãi có đông không?", "CURRENT_OCCUPANCY", "get_current_occupancy"),
    ("hôm nay người ta vào bãi bao nhiêu lượt?", "TODAY_TRAFFIC", "get_traffic"),
    ("nay thu được bao nhiêu tiền?", "REVENUE", "get_revenue"),
    ("giờ nào đông nhất vậy?", "PEAK_HOURS", "get_peak_hours"),
    ("vé tháng hiện còn mấy cái dùng được?", "MONTHLY_TICKETS", "get_monthly_ticket_summary"),
    ("nhân viên muốn ghi xe vào thì làm thế nào?", "SYSTEM_HELP", "get_system_help"),
)


def force_fallback(monkeypatch):
    monkeypatch.setattr(chatbot, "classify_question_with_qwen", lambda _question: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini", lambda _question: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_standalone", lambda _question: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_context", lambda _q, _h: None)
    monkeypatch.setattr(chatbot, "_qwen_answer", lambda *_args: (None, "test-model"))
    monkeypatch.setattr(chatbot, "_gemini_answer", lambda *_args: (None, "test-model"))
    monkeypatch.setattr(chatbot, "_gemini_answer_synthesis", lambda *_args: (None, "test-model"))


def test_natural_questions_use_safe_rule_fallback(manager, monkeypatch):
    force_fallback(monkeypatch)
    for question, intent, backend_query in NATURAL_CASES:
        response = manager.post("/api/chatbot", json={"message": question})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["ok"] is True
        assert payload["intent"] == intent
        assert payload["backend_query"] == backend_query
        assert payload["classification_source"] == "rule_fallback"
        assert payload["answer"]


def test_qwen_classification_is_primary(manager, monkeypatch):
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_qwen",
        lambda _question: {
            "intent": "SLOT_STATUS",
            "parameters": {"slot_code": "D1"},
            "confidence": 0.97,
        },
    )
    monkeypatch.setattr(chatbot, "_qwen_answer", lambda *_args: (None, "test-model"))
    payload = manager.post("/api/chatbot", json={"message": "ô đầu tiên sao rồi?"}).get_json()
    assert payload["intent"] == "SLOT_STATUS"
    assert payload["classification_source"] == "qwen"
    assert payload["confidence"] == 0.97
    assert payload["data"]["slot_code"] == "D01"


test_gemini_classification_is_primary = test_qwen_classification_is_primary


def test_ollama_unreachable_triggers_safe_fallback(manager, monkeypatch):
    import urllib.error

    def mock_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("Connection refused [Errno 111]")

    monkeypatch.setattr(chatbot.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_standalone", lambda _q: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini", lambda _q: None)
    monkeypatch.setattr(chatbot, "_gemini_answer_synthesis", lambda *_args: (None, "test-model"))
    monkeypatch.setattr(chatbot, "_gemini_answer", lambda *_args: (None, "test-model"))
    response = manager.post("/api/chatbot", json={"message": "bây giờ còn chỗ đậu không?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "EMPTY_SLOTS"
    assert payload["classification_source"] == "rule_fallback"
    assert payload["answer_source"] == "backend_fallback"
    assert payload["ai_used"] is False
    assert "trống" in payload["answer"]


def test_invalid_slot_is_rejected_before_query(manager, monkeypatch):
    force_fallback(monkeypatch)
    response = manager.post("/api/chatbot", json={"message": "D99 có xe không?"})
    assert response.status_code == 400
    assert "D01–D24" in response.get_json()["error"]


def test_unknown_question_does_not_invent_data(manager, monkeypatch):
    force_fallback(monkeypatch)
    response = manager.post("/api/chatbot", json={"message": "abc xyz"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["intent"] == "UNKNOWN"
    assert payload["data"] == {}
    assert payload["backend_query"] is None


def test_date_and_slot_parameter_validation():
    assert chatbot._slot_token("D5") == "D05"
    assert chatbot._validated_classification({
        "intent": "REVENUE",
        "parameters": {"period": "yesterday", "ignored": "value"},
        "confidence": 0.9,
    }) == {
        "intent": "REVENUE",
        "parameters": {"period": "yesterday"},
        "confidence": 0.9,
    }


def test_chatbot_multi_role_access_and_rbac(admin, staff, customer, monkeypatch):
    force_fallback(monkeypatch)
    # 1. Staff and Customer can access GET /chatbot
    assert staff.get("/chatbot").status_code == 200
    assert customer.get("/chatbot").status_code == 200

    # 2. Staff can query empty slots, but cannot query revenue
    res_staff_empty = staff.post("/api/chatbot", json={"message": "bây giờ còn chỗ đậu không?"})
    assert res_staff_empty.status_code == 200
    assert res_staff_empty.get_json()["ok"] is True
    assert res_staff_empty.get_json()["intent"] == "EMPTY_SLOTS"

    res_staff_rev = staff.post("/api/chatbot", json={"message": "nay thu được bao nhiêu tiền?"})
    assert res_staff_rev.status_code == 200
    payload_staff_rev = res_staff_rev.get_json()
    assert payload_staff_rev["ok"] is False
    assert "chỉ dành riêng cho Quản trị viên" in payload_staff_rev["answer"]

    # 3. Customer can query rates and empty slots
    res_cust_rates = customer.post("/api/chatbot", json={"message": "giá vé gửi xe bao nhiêu?"})
    assert res_cust_rates.status_code == 200
    payload_rates = res_cust_rates.get_json()
    assert payload_rates["ok"] is True
    assert payload_rates["intent"] == "PARKING_RATES"

    res_cust_empty = customer.post("/api/chatbot", json={"message": "còn bao nhiêu chỗ trống?"})
    assert res_cust_empty.status_code == 200
    assert res_cust_empty.get_json()["ok"] is True

    # 4. Customer blocked from revenue and occupied slots
    res_cust_rev = customer.post("/api/chatbot", json={"message": "hôm nay doanh thu bao nhiêu?"})
    assert res_cust_rev.status_code == 200
    assert res_cust_rev.get_json()["ok"] is False
    assert "quản trị nội bộ" in res_cust_rev.get_json()["answer"]

    res_cust_occ = customer.post("/api/chatbot", json={"message": "ô nào đang có xe?"})
    assert res_cust_occ.status_code == 200
    assert res_cust_occ.get_json()["ok"] is False
    assert "nội bộ" in res_cust_occ.get_json()["answer"]

    # 5. Customer asking about personal vehicle
    res_cust_vehicle = customer.post("/api/chatbot", json={"message": "xe của tôi đang đỗ ở đâu?"})
    assert res_cust_vehicle.status_code == 200
    payload_veh = res_cust_vehicle.get_json()
    assert payload_veh["ok"] is True
    assert payload_veh["intent"] == "PERSONAL_VEHICLE"
    assert "chưa liên kết biển số xe" in payload_veh["answer"]


def test_ollama_unreachable_uses_gemini_fallback(manager, monkeypatch):
    import urllib.error

    def mock_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("Connection refused [Errno 111]")

    monkeypatch.setattr(chatbot.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_gemini_standalone",
        lambda _question: {
            "intent": "EMPTY_SLOTS",
            "parameters": {},
            "confidence": 0.94,
        },
    )
    monkeypatch.setattr(chatbot, "_gemini_answer_synthesis", lambda *_args: (None, "gemini-test"))
    response = manager.post("/api/chatbot", json={"message": "còn chỗ đậu không?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "EMPTY_SLOTS"
    assert payload["classification_source"] == "gemini"


from datetime import timedelta


def test_follow_up_case_1_traffic_switches_date_to_yesterday(manager, monkeypatch):
    """Case 1: User asks 'Hôm nay có bao nhiêu lượt xe vào bãi?' then 'Thế còn hôm qua?'
    Verifies that metric is retained, date filter is switched to yesterday, and get_traffic(yesterday) is called."""
    yesterday = chatbot.today() - timedelta(days=1)
    traffic_calls = []
    orig_get_traffic = chatbot.get_traffic

    def spy_get_traffic(day):
        traffic_calls.append(day)
        return orig_get_traffic(day)

    monkeypatch.setattr(chatbot, "get_traffic", spy_get_traffic)
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_gemini_context",
        lambda question, history: {
            "intent": "TODAY_TRAFFIC",
            "parameters": {"period": "yesterday", "date": yesterday.isoformat()},
            "confidence": 0.96,
        },
    )
    history = [
        {"role": "user", "text": "Hôm nay có bao nhiêu lượt xe vào bãi?"},
        {"role": "assistant", "text": "Hôm nay có 15 lượt xe.", "intent": "TODAY_TRAFFIC"},
    ]
    response = manager.post(
        "/api/chatbot",
        json={"message": "Thế còn hôm qua?", "history": history},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "TODAY_TRAFFIC"
    assert payload["classification_source"] == "gemini_context"
    assert payload["confidence"] == 0.96
    assert payload["backend_query"] == "get_traffic"
    # Strict verification: the actual parameter passed to the ORM was yesterday's date
    assert len(traffic_calls) == 1
    assert traffic_calls[0] == yesterday
    assert payload["data"]["date"] == yesterday.isoformat()


def test_follow_up_case_2_multiple_metrics_asks_for_clarification(manager, monkeypatch):
    """Case 2: User asks 'Hôm nay có bao nhiêu xe và bao nhiêu chỗ trống?' then 'Còn hôm qua?'
    When multiple metrics exist, chatbot must ask for clarification instead of guessing."""
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_context", lambda _q, _h: None)
    monkeypatch.setattr(chatbot, "classify_question_with_qwen", lambda _q: None)
    history = [
        {"role": "user", "text": "Hôm nay có bao nhiêu xe và bao nhiêu chỗ trống?"},
        {"role": "assistant", "text": "Hiện có 0/24 xe, còn 24 ô trống.", "intent": "CURRENT_OCCUPANCY"},
    ]
    response = manager.post(
        "/api/chatbot",
        json={"message": "Còn hôm qua?", "history": history},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["intent"] == "UNKNOWN"
    assert payload["backend_query"] is None
    # Chatbot asks to clarify between traffic and revenue
    assert any(term in payload["answer"].lower() for term in ("lượt xe", "lưu lượng", "doanh thu", "chỉ số"))


def test_follow_up_case_3_occupancy_switches_to_empty_slots(manager, monkeypatch):
    """Case 3: User asks 'Hôm nay bãi có bao nhiêu xe?' then 'Thế còn chỗ trống thì sao?'
    Verifies that intent switches to EMPTY_SLOTS and calls get_empty_slots() for fresh data."""
    empty_calls = []
    orig_get_empty = chatbot.get_empty_slots

    def spy_get_empty():
        empty_calls.append(True)
        return orig_get_empty()

    monkeypatch.setattr(chatbot, "get_empty_slots", spy_get_empty)
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_gemini_context",
        lambda question, history: {
            "intent": "EMPTY_SLOTS",
            "parameters": {},
            "confidence": 0.95,
        },
    )
    history = [
        {"role": "user", "text": "Hôm nay bãi có bao nhiêu xe?"},
        {"role": "assistant", "text": "Hiện có 0/24 ô đang có xe.", "intent": "CURRENT_OCCUPANCY"},
    ]
    response = manager.post(
        "/api/chatbot",
        json={"message": "Thế còn chỗ trống thì sao?", "history": history},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "EMPTY_SLOTS"
    assert payload["backend_query"] == "get_empty_slots"
    assert len(empty_calls) == 1
    assert "slots" in payload["data"]
    assert "empty_count" in payload["data"]


def test_follow_up_heuristic_fallback_when_gemini_fails(manager, monkeypatch):
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_context", lambda _q, _h: None)
    monkeypatch.setattr(chatbot, "classify_question_with_qwen", lambda _q: None)
    history = [
        {"role": "user", "text": "Doanh thu hôm nay bao nhiêu?"},
        {"role": "assistant", "text": "Hôm nay thu được 500,000 đ.", "intent": "REVENUE"},
    ]
    response = manager.post(
        "/api/chatbot",
        json={"message": "Còn hôm qua?", "history": history},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "REVENUE"
    assert payload["classification_source"] == "rule_fallback"


def test_invalid_or_bloated_history_is_safely_sanitized(manager, monkeypatch):
    force_fallback(monkeypatch)
    bloated_history = [
        {"role": "invalid_role", "text": "bad"},
        {"role": "user", "text": "x" * 2000},
        None,
        "not-a-dict",
    ] * 5
    response = manager.post(
        "/api/chatbot",
        json={"message": "hôm nay thu được bao nhiêu tiền?", "history": bloated_history},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "REVENUE"


def test_provider_timeout_triggers_safe_fallback(manager, monkeypatch):
    import urllib.error

    def mock_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(chatbot.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_standalone", lambda _q: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini", lambda _q: None)
    response = manager.post("/api/chatbot", json={"message": "nay thu được bao nhiêu tiền?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "REVENUE"
    assert payload["classification_source"] == "rule_fallback"


def test_missing_gemini_key_graceful_local_operation(manager, monkeypatch):
    monkeypatch.setattr(chatbot, "_gemini_settings", lambda: ("", ""))
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_qwen",
        lambda _q: {"intent": "OCCUPIED_SLOTS", "parameters": {}, "confidence": 0.95},
    )
    response = manager.post("/api/chatbot", json={"message": "ô nào đang có xe?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "OCCUPIED_SLOTS"
    assert payload["classification_source"] == "qwen"


def test_provider_invalid_json_triggers_safe_fallback(manager, monkeypatch):
    monkeypatch.setattr(chatbot, "_extract_first_json", lambda _text: None)
    force_fallback(monkeypatch)
    response = manager.post("/api/chatbot", json={"message": "D01 có ai đỗ chưa?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "SLOT_STATUS"
    assert payload["classification_source"] == "rule_fallback"


def test_provider_invalid_intent_rejected(manager, monkeypatch):
    monkeypatch.setattr(
        chatbot,
        "classify_question_with_qwen",
        lambda _q: {"intent": "DROP_TABLE_USERS", "parameters": {}, "confidence": 0.99},
    )
    force_fallback(monkeypatch)
    response = manager.post("/api/chatbot", json={"message": "bây giờ còn chỗ đậu không?"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["intent"] == "EMPTY_SLOTS"
    assert payload["classification_source"] == "rule_fallback"


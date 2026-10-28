import pytest
from sqlalchemy import select
from app.extensions import db
from app.models import User, Slot, Visit

OPERATIONAL_WEB_URLS = [
    '/slots',
    '/entry',
    '/exit',
    '/visits',
]

ADMIN_WEB_URLS = [
    '/rates',
    '/areas',
    '/users',
    '/statistics',
    '/backups',
]

ADMIN_API_URLS = [
    ('/api/rates', {'turn_fee': 25000, 'night_fee': 90000}),
    ('/api/areas/D', {'name': 'Khu D Mới', 'description': 'Mô tả'}),
    ('/api/users', {'username': 'teststaff99', 'full_name': 'Test Staff', 'password': 'strong-password-123'}),
    ('/api/backups', {'confirmed': True}),
]


def test_admin_has_full_operational_and_admin_access(admin):
    """1. ADMIN được phép truy cập tất cả chức năng quản trị và vận hành."""
    assert admin.get('/').status_code == 200
    assert admin.get('/chatbot').status_code == 200
    for url in OPERATIONAL_WEB_URLS:
        res = admin.get(url)
        assert res.status_code == 200, f"Admin must have access to {url}, got {res.status_code}"
    
    for url in ADMIN_WEB_URLS:
        res = admin.get(url)
        assert res.status_code == 200, f"Admin must have access to {url}, got {res.status_code}"

    # Operational API
    assert admin.get('/api/slots').status_code == 200
    assert admin.get('/api/plate-check?plate=30A12345').status_code == 200


def test_legacy_manager_transition_access(legacy_manager):
    """8. Tài khoản MANAGER cũ không bị mất quyền trong giai đoạn chuyển tiếp."""
    assert legacy_manager.get('/').status_code == 200
    assert legacy_manager.get('/chatbot').status_code == 200
    for url in OPERATIONAL_WEB_URLS:
        assert legacy_manager.get(url).status_code == 200
    for url in ADMIN_WEB_URLS:
        assert legacy_manager.get(url).status_code == 200


def test_staff_operational_access_and_admin_block(staff):
    """2. STAFF được phép truy cập vận hành và chatbot nhưng bị chặn khỏi quản trị."""
    assert staff.get('/').status_code == 200
    assert staff.get('/chatbot').status_code == 200
    for url in OPERATIONAL_WEB_URLS:
        assert staff.get(url).status_code == 200, f"Staff should access {url}"
    
    assert staff.get('/api/slots').status_code == 200
    assert staff.get('/api/plate-check?plate=30A12345').status_code == 200
    
    for url in ADMIN_WEB_URLS:
        res = staff.get(url)
        assert res.status_code == 403, f"Staff must be blocked from {url}, got {res.status_code}"
        
    for url, payload in ADMIN_API_URLS:
        res = staff.post(url, json=payload)
        assert res.status_code == 403, f"Staff must be blocked from {url}, got {res.status_code}"


def test_customer_portal_and_operational_block(customer):
    """3. CUSTOMER xem được Customer Portal và Chatbot nhưng bị chặn khỏi vận hành & quản trị."""
    # Customer can access Customer Dashboard and Chatbot
    dash_res = customer.get('/')
    assert dash_res.status_code == 200
    dash_text = dash_res.data.decode('utf-8')
    assert 'DỊCH VỤ KHÁCH HÀNG' in dash_text
    assert 'Chào mừng, Khách hàng kiểm thử' in dash_text
    assert 'Chỗ trống hiện tại' in dash_text
    assert 'Vé lượt ban ngày' in dash_text
    # Customer dashboard must NOT leak revenue or operational actions
    assert 'TRUNG TÂM VẬN HÀNH' not in dash_text
    assert 'Doanh thu hôm nay' not in dash_text
    assert '+ Ghi nhận xe vào' not in dash_text
    assert 'Xe ra ↗' not in dash_text

    chatbot_res = customer.get('/chatbot')
    assert chatbot_res.status_code == 200
    assert 'DỊCH VỤ KHÁCH HÀNG' in chatbot_res.data.decode('utf-8')

    # Blocked from operational web pages
    for url in OPERATIONAL_WEB_URLS:
        res = customer.get(url)
        assert res.status_code == 403, f"Customer must get 403 on {url}, got {res.status_code}"
        
    # Blocked from admin web pages
    for url in ADMIN_WEB_URLS:
        res = customer.get(url)
        assert res.status_code == 403, f"Customer must get 403 on {url}, got {res.status_code}"

    # Blocked from operational APIs
    operational_api_checks = [
        ('get', '/api/slots', None),
        ('get', '/api/plate-check?plate=30A12345', None),
        ('post', '/api/visits', {'plate': '30A12345', 'slot_code': 'D01', 'plate_confirmed': True, 'slot_confirmed': True}),
        ('get', '/api/visits/lookup?plate=30A12345', None),
        ('get', '/api/visits/1/quote', None),
        ('post', '/api/visits/1/checkout', {'quoted_amount': 20000, 'vehicle_confirmed': True, 'paid_confirmed': True}),
        ('post', '/api/visits/1/move', {'slot_code': 'D02'}),
        ('post', '/api/slots/D01/confirm', {'state': 'EMPTY'}),
        ('post', '/api/vision/parking', {}),
        ('post', '/api/vision/plate', {}),
        ('get', '/api/vision/status', None),
        ('post', '/api/vision/video', {}),
        ('get', '/api/vision/video/status', None),
        ('get', '/api/vision/video/states', None),
        ('get', '/api/vision/video/detections', None),
        ('get', '/api/ai-demo/states', None),
    ]

    for method, endpoint, payload in operational_api_checks:
        if method == 'get':
            res = customer.get(endpoint)
        else:
            res = customer.post(endpoint, json=payload or {})
        assert res.status_code == 403, f"Customer must get 403 on {endpoint}, got {res.status_code}"

    # Blocked from admin APIs
    for url, payload in ADMIN_API_URLS:
        res = customer.post(url, json=payload)
        assert res.status_code == 403, f"Customer must get 403 on {url}, got {res.status_code}"


def test_unauthenticated_user_access_control(client):
    """4. Người chưa đăng nhập bị chuyển hướng đến /login."""
    for url in ['/', '/chatbot'] + OPERATIONAL_WEB_URLS + ADMIN_WEB_URLS:
        res = client.get(url)
        assert res.status_code == 302
        assert '/login' in res.headers.get('Location', '')

    res = client.get('/register')
    assert res.status_code == 200


def test_customer_registration_fixed_role_and_no_privilege_escalation(client, app):
    """5. Đăng ký khách hàng gán cứng vai trò CUSTOMER, không thể leo thang đặc quyền."""
    payload = {
        'username': 'attacker_admin',
        'full_name': 'Attacker Escalation',
        'password': 'safe-password-12345',
        'confirm_password': 'safe-password-12345',
        'role': 'ADMIN',  # Forged role injection attempt
    }
    res = client.post('/api/register', json=payload)
    assert res.status_code == 200, res.data.decode()
    assert res.json['redirect'] == '/login'

    with app.app_context():
        user = db.session.scalar(select(User).where(User.username == 'attacker_admin'))
        assert user is not None
        assert user.role == 'CUSTOMER', f"Role must be strictly 'CUSTOMER', got '{user.role}'"
        assert user.check_password('safe-password-12345')


def test_registration_validation_and_duplicate_handling(client):
    """6. Kiểm tra tính hợp lệ và từ chối tên đăng nhập trùng lặp."""
    # Valid first registration
    res1 = client.post('/api/register', json={
        'username': 'john_doe',
        'full_name': 'John Doe',
        'password': 'valid-password-123',
        'confirm_password': 'valid-password-123',
    })
    assert res1.status_code == 200

    # Duplicate username returns 409 Conflict
    res_dup = client.post('/api/register', json={
        'username': 'john_doe',
        'full_name': 'Another John',
        'password': 'valid-password-123',
        'confirm_password': 'valid-password-123',
    })
    assert res_dup.status_code == 409
    assert 'đã tồn tại' in res_dup.json.get('error', '')

    # Password mismatch returns 400
    res_mismatch = client.post('/api/register', json={
        'username': 'jane_doe',
        'full_name': 'Jane Doe',
        'password': 'password-12345',
        'confirm_password': 'different-password',
    })
    assert res_mismatch.status_code == 400

    # Password too short (< 10 chars) returns 400
    res_short = client.post('/api/register', json={
        'username': 'short_pass',
        'full_name': 'Short Pass',
        'password': 'short',
        'confirm_password': 'short',
    })
    assert res_short.status_code == 400

    # Empty full name returns 400
    res_empty_name = client.post('/api/register', json={
        'username': 'empty_name',
        'full_name': '   ',
        'password': 'valid-password-123',
        'confirm_password': 'valid-password-123',
    })
    assert res_empty_name.status_code == 400


def test_registration_rate_limiting(client):
    """Rate limit chặn sau 5 lần thử trong vòng 5 phút."""
    from app.routes import _register_attempts
    _register_attempts.clear()
    # Send 5 registration requests with different invalid usernames to trigger rate limiter
    for i in range(5):
        client.post('/api/register', json={
            'username': f'rate_user_{i}',
            'full_name': 'Rate Limit Test',
            'password': 'valid-password-123',
            'confirm_password': 'valid-password-123',
        })
    # The 6th attempt from same IP must be rate limited (429)
    res_limit = client.post('/api/register', json={
        'username': 'rate_user_excess',
        'full_name': 'Rate Limit Test',
        'password': 'valid-password-123',
        'confirm_password': 'valid-password-123',
    })
    assert res_limit.status_code == 429
    _register_attempts.clear()


def test_registration_csrf_protection(app):
    """Xác nhận CSRF bảo vệ route đăng ký /api/register khi bật CSRF."""
    import re
    from app.routes import _register_attempts
    _register_attempts.clear()
    app.config['WTF_CSRF_ENABLED'] = True
    with app.test_client() as client:
        # Request without CSRF token is rejected with 400
        res_no_csrf = client.post('/api/register', json={
            'username': 'csrf_user_fail',
            'full_name': 'CSRF Test',
            'password': 'valid-password-123',
            'confirm_password': 'valid-password-123',
        })
        assert res_no_csrf.status_code == 400

        # Request with valid CSRF token from /register succeeds
        page = client.get('/register')
        token = re.search(r'name="csrf-token" content="([^"]+)"', page.data.decode()).group(1)
        res_with_csrf = client.post(
            '/api/register',
            json={
                'username': 'csrf_user_success',
                'full_name': 'CSRF Test',
                'password': 'valid-password-123',
                'confirm_password': 'valid-password-123',
            },
            headers={'X-CSRFToken': token}
        )
        assert res_with_csrf.status_code == 200
        _register_attempts.clear()


def test_customer_cannot_guess_visit_ids(customer):
    """Khách hàng không thể xem hoặc thao tác trên lượt gửi của tài khoản khác bằng cách đoán ID."""
    for visit_id in [1, 2, 999]:
        res_quote = customer.get(f'/api/visits/{visit_id}/quote')
        assert res_quote.status_code == 403
        res_checkout = customer.post(f'/api/visits/{visit_id}/checkout', json={'quoted_amount': 20000, 'vehicle_confirmed': True, 'paid_confirmed': True})
        assert res_checkout.status_code == 403
        res_move = customer.post(f'/api/visits/{visit_id}/move', json={'slot_code': 'D02'})
        assert res_move.status_code == 403


def test_chatbot_role_matrix_and_phrase_variations(admin, staff, customer, monkeypatch):
    """Kiểm tra ma trận quyền chatbot cho 3 vai trò và các cách diễn đạt câu hỏi khác nhau."""
    from app.services import chatbot
    monkeypatch.setattr(chatbot, "classify_question_with_qwen", lambda _q: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini", lambda _q: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_standalone", lambda _q: None)
    monkeypatch.setattr(chatbot, "classify_question_with_gemini_context", lambda _q, _h: None)
    monkeypatch.setattr(chatbot, "_qwen_answer", lambda *_args: (None, "test-model"))
    monkeypatch.setattr(chatbot, "_gemini_answer", lambda *_args: (None, "test-model"))
    monkeypatch.setattr(chatbot, "_gemini_answer_synthesis", lambda *_args: (None, "test-model"))

    # 1. ADMIN hỏi doanh thu bằng nhiều cách diễn đạt: được trả lời
    admin_rev_phrases = [
        "nay thu được bao nhiêu tiền?",
        "doanh thu ngày hôm nay là bao nhiêu?",
        "tiền thu được hôm nay",
    ]
    for phrase in admin_rev_phrases:
        res = admin.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is True
        assert payload['intent'] == 'REVENUE'
        assert payload['backend_query'] == 'get_revenue'

    # 2. STAFF hỏi doanh thu bằng nhiều cách diễn đạt: bị từ chối, backend_query là None
    for phrase in admin_rev_phrases:
        res = staff.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is False
        assert payload['backend_query'] is None
        assert 'chỉ dành riêng cho Quản trị viên' in payload['answer']

    # 3. CUSTOMER hỏi doanh thu bằng nhiều cách diễn đạt: bị từ chối, backend_query là None
    for phrase in admin_rev_phrases:
        res = customer.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is False
        assert payload['backend_query'] is None
        assert 'quản trị nội bộ' in payload['answer']

    # 4. CUSTOMER hỏi danh sách xe đang đỗ / ô có xe / lưu lượng: bị từ chối
    cust_blocked_phrases = [
        ("ô nào đang có xe?", "OCCUPIED_SLOTS"),
        ("những ô nào đã có xe đỗ?", "OCCUPIED_SLOTS"),
        ("D01 có ai đỗ chưa?", "SLOT_STATUS"),
        ("hôm nay lưu lượng xe thế nào?", "TODAY_TRAFFIC"),
        ("giờ cao điểm hôm nay lúc nào?", "PEAK_HOURS"),
    ]
    for phrase, expected_intent in cust_blocked_phrases:
        res = customer.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is False
        assert payload['backend_query'] is None
        assert 'vận hành nội bộ' in payload['answer'] or 'nội bộ' in payload['answer']

    # 5. CUSTOMER hỏi số chỗ trống / biểu phí: được phép công khai
    cust_allowed_phrases = [
        ("bây giờ còn chỗ đậu không?", "EMPTY_SLOTS"),
        ("những ô nào chưa có xe?", "EMPTY_SLOTS"),
        ("giá vé gửi xe bao nhiêu?", "PARKING_RATES"),
        ("bảng giá gửi xe", "PARKING_RATES"),
        ("hướng dẫn gửi xe", "SYSTEM_HELP"),
    ]
    for phrase, expected_intent in cust_allowed_phrases:
        res = customer.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is True
        assert payload['intent'] == expected_intent

    # 6. CUSTOMER hỏi xe của mình: trả về giới hạn thân thiện rõ ràng
    customer_car_phrases = [
        "xe của tôi đang ở đâu?",
        "tìm xe của tôi",
        "xe tôi gửi lúc mấy giờ",
        "biển số của tôi có trong bãi không?",
    ]
    for phrase in customer_car_phrases:
        res = customer.post('/api/chatbot', json={'message': phrase})
        assert res.status_code == 200
        payload = res.get_json()
        assert payload['ok'] is True
        assert payload['intent'] == 'PERSONAL_VEHICLE'
        assert 'chưa liên kết biển số xe' in payload['answer']
        assert payload['data']['limitation'] == 'no_account_vehicle_link'

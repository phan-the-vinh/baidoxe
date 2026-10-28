import re
import json
from functools import wraps
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import abort
from flask_login import current_user, login_required
from sqlalchemy import select
from ..extensions import db
from ..models import SiteLock, AuditLog

VN = ZoneInfo('Asia/Ho_Chi_Minh')

class BusinessError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def admin_only(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if getattr(current_user, 'role', None) not in ('ADMIN', 'MANAGER'):
            abort(403)
        return fn(*args, **kwargs)
    return wrapped

# Bí danh tương thích cho giai đoạn chuyển đổi
manager_only = admin_only

def staff_or_admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if getattr(current_user, 'role', None) not in ('ADMIN', 'STAFF', 'MANAGER'):
            abort(403)
        return fn(*args, **kwargs)
    return wrapped

def local_time(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(VN)

def today():
    return datetime.now(VN).date()

def parse_date(value):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise BusinessError('Ngày phải có dạng YYYY-MM-DD.') from None

def normalize_plate(value):
    plate = re.sub(r'[\s.\-]', '', str(value or '')).upper()
    # Ho tro bien 4/5 so cuoi, bien dac biet chu 1-3 ky tu. Khong xac thuc giay dang ky.
    if not re.fullmatch(r'[0-9]{2}[A-Z]{1,3}[0-9]{4,6}', plate):
        raise BusinessError('Biển số chưa hợp lệ. Ví dụ: 30A-123.45 hoặc 29LD-12345.')
    return plate

def field(data, key, max_length=120):
    value = str(data.get(key) or '').strip()
    if not value or len(value) > max_length:
        raise BusinessError(f'Trường {key} bắt buộc và tối đa {max_length} ký tự.')
    return value

def integer(data, key, minimum=0, maximum=100_000_000):
    raw = str(data.get(key, ''))
    if not re.fullmatch(r'\d+', raw):
        raise BusinessError(f'{key}: nhập số nguyên không âm.')
    value = int(raw)
    if not minimum <= value <= maximum:
        raise BusinessError(f'{key}: giá trị ngoài khoảng {minimum}–{maximum}.')
    return value

def confirmed(data, key):
    if data.get(key) not in (True, 'true', 'on', '1', 1):
        raise BusinessError('Hãy kiểm tra và đánh dấu xác nhận trước khi lưu.')

def write_lock():
    """Khoa giao dich toan bai nho: ngan double-entry, thanh toan kep, trung ky ve.

    SQL Server giu UPDLOCK/HOLDLOCK den commit/rollback. SQLite chi cho tests.
    Phai goi truoc moi truy van du lieu nghiep vu trong mutation.
    """
    query = select(SiteLock).where(SiteLock.id == 1)
    if db.engine.dialect.name == 'mssql':
        query = query.with_hint(SiteLock, 'WITH (UPDLOCK, HOLDLOCK)', dialect_name='mssql')
    elif not __import__('flask').current_app.testing:
        raise RuntimeError('Ứng dụng thật chỉ sử dụng SQL Server.')
    lock = db.session.execute(query.execution_options(populate_existing=True)).scalar_one()
    lock.revision += 1
    db.session.flush()

def audit(actor_id, action, detail):
    db.session.add(AuditLog(actor_id=actor_id, action=action,
                            detail=json.dumps(detail, ensure_ascii=False)))

from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import Index, CheckConstraint, text
from sqlalchemy.dialects.mssql import DATETIME2
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

LEGACY_SLOT_CODES = tuple(f'{area}{number:02d}' for area in 'AB' for number in range(1, 11))
TARGET_SLOT_CODES = tuple(f'D{number:02d}' for number in range(1, 25))

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

STAMP = db.DateTime().with_variant(DATETIME2(precision=6), 'mssql')

class SiteLock(db.Model):
    __tablename__ = 'site_lock'
    id = db.Column(db.Integer, primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=0)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, unique=True)
    full_name = db.Column(db.Unicode(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("role IN ('ADMIN','STAFF','CUSTOMER','MANAGER')", name='ck_user_role'),)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Area(db.Model):
    __tablename__ = 'areas'
    code = db.Column(db.String(1), primary_key=True)
    name = db.Column(db.Unicode(60), nullable=False)
    description = db.Column(db.Unicode(500), nullable=False, default='')
    # Metadata remains transitional so the same web build can read the legacy
    # A/B database before cutover and the D layout after the approved migration.
    __table_args__ = (CheckConstraint("code IN ('A','B','D')", name='ck_fixed_area'),)

class Slot(db.Model):
    __tablename__ = 'slots'
    code = db.Column(db.String(3), primary_key=True)
    area_code = db.Column(db.String(1), db.ForeignKey('areas.code'), nullable=False)
    state = db.Column(db.String(8), nullable=False, default='EMPTY')
    needs_review = db.Column(db.Boolean, nullable=False, default=False)
    changed_at = db.Column(STAMP, nullable=False, default=utcnow)
    ai_state = db.Column(db.String(8))
    ai_confidence = db.Column(db.Float)
    ai_at = db.Column(STAMP)
    __table_args__ = (
        CheckConstraint("state IN ('EMPTY','OCCUPIED')", name='ck_slot_state'),
        CheckConstraint(
            "(area_code='A' AND code IN ('A01','A02','A03','A04','A05','A06','A07','A08','A09','A10')) "
            "OR (area_code='B' AND code IN ('B01','B02','B03','B04','B05','B06','B07','B08','B09','B10')) "
            "OR (area_code='D' AND code IN ('D01','D02','D03','D04','D05','D06','D07','D08','D09','D10','D11','D12','D13','D14','D15','D16','D17','D18','D19','D20','D21','D22','D23','D24'))",
            name='ck_fixed_slot'
        ),
    )

class ParkingCapacityHistory(db.Model):
    __tablename__ = 'parking_capacity_history'
    effective_at = db.Column(STAMP, primary_key=True)
    capacity = db.Column(db.SmallInteger, nullable=False)
    reason = db.Column(db.String(40), nullable=False, unique=True)
    __table_args__ = (
        CheckConstraint('capacity BETWEEN 1 AND 10000', name='ck_parking_capacity_history_capacity'),
    )

class Rate(db.Model):
    __tablename__ = 'rates'
    id = db.Column(db.Integer, primary_key=True)
    turn_fee = db.Column(db.Integer, nullable=False)
    night_fee = db.Column(db.Integer, nullable=False)
    monthly_fee = db.Column(db.Integer)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    __table_args__ = (CheckConstraint('turn_fee>=0 AND night_fee>=0 AND (monthly_fee IS NULL OR monthly_fee>=0)', name='ck_rate_nonnegative'),)

class MonthlyPass(db.Model):
    __tablename__ = 'monthly_passes'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True)
    plate = db.Column(db.String(16), nullable=False, unique=True)
    owner_name = db.Column(db.Unicode(120), nullable=False)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)
    periods = db.relationship('PassPeriod', backref='monthly_pass', lazy='select', order_by='PassPeriod.start_date')

class PassPeriod(db.Model):
    __tablename__ = 'pass_periods'
    id = db.Column(db.Integer, primary_key=True)
    pass_id = db.Column(db.Integer, db.ForeignKey('monthly_passes.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    __table_args__ = (CheckConstraint('end_date>=start_date AND amount>=0', name='ck_pass_period'),)

class Visit(db.Model):
    __tablename__ = 'visits'
    id = db.Column(db.Integer, primary_key=True)
    ticket_code = db.Column(db.String(32), nullable=False, unique=True)
    plate = db.Column(db.String(16), nullable=False)
    slot_code = db.Column(db.String(3), db.ForeignKey('slots.code'), nullable=False)
    entered_at = db.Column(STAMP, nullable=False, default=utcnow)
    exited_at = db.Column(STAMP)
    ticket_type = db.Column(db.String(8), nullable=False)
    monthly_period_id = db.Column(db.Integer, db.ForeignKey('pass_periods.id'))
    rate_id = db.Column(db.Integer, db.ForeignKey('rates.id'), nullable=False)
    rate = db.relationship('Rate')
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    exited_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    amount = db.Column(db.Integer)
    fee_breakdown = db.Column(db.UnicodeText)
    lost_ticket = db.Column(db.Boolean, nullable=False, default=False)
    verification_note = db.Column(db.Unicode(500))
    __table_args__ = (
        Index('uq_active_plate', 'plate', unique=True, mssql_where=text('exited_at IS NULL'), sqlite_where=text('exited_at IS NULL')),
        Index('uq_active_slot', 'slot_code', unique=True, mssql_where=text('exited_at IS NULL'), sqlite_where=text('exited_at IS NULL')),
        Index('ix_visit_entry', 'entered_at'),
        Index('ix_visit_exit', 'exited_at'),
        CheckConstraint("ticket_type IN ('TURN','MONTH')", name='ck_ticket_type'),
        CheckConstraint('exited_at IS NULL OR exited_at>=entered_at', name='ck_visit_time'),
        CheckConstraint('amount IS NULL OR amount>=0', name='ck_visit_amount'),
    )

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    visit_id = db.Column(db.Integer, db.ForeignKey('visits.id'))
    period_id = db.Column(db.Integer, db.ForeignKey('pass_periods.id'))
    amount = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(8), nullable=False)
    paid_at = db.Column(STAMP, nullable=False, default=utcnow)
    cashier_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    __table_args__ = (
        Index('uq_payment_visit', 'visit_id', unique=True, mssql_where=text('visit_id IS NOT NULL'), sqlite_where=text('visit_id IS NOT NULL')),
        Index('uq_payment_period', 'period_id', unique=True, mssql_where=text('period_id IS NOT NULL'), sqlite_where=text('period_id IS NOT NULL')),
        CheckConstraint('amount>=0', name='ck_payment_amount'),
        CheckConstraint("(kind='TURN' AND visit_id IS NOT NULL AND period_id IS NULL) OR (kind='MONTH' AND period_id IS NOT NULL AND visit_id IS NULL)", name='ck_payment_source'),
    )

class Observation(db.Model):
    __tablename__ = 'observations'
    id = db.Column(db.Integer, primary_key=True)
    slot_code = db.Column(db.String(3), db.ForeignKey('slots.code'), nullable=False)
    captured_at = db.Column(STAMP, nullable=False)
    state = db.Column(db.String(8), nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    accepted = db.Column(db.Boolean, nullable=False)
    reason = db.Column(db.Unicode(250), nullable=False)

class Alert(db.Model):
    __tablename__ = 'alerts'
    id = db.Column(db.Integer, primary_key=True)
    slot_code = db.Column(db.String(3), db.ForeignKey('slots.code'), nullable=False)
    reason = db.Column(db.Unicode(250), nullable=False)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)
    resolved_at = db.Column(STAMP)
    resolved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    resolution = db.Column(db.Unicode(500))
    __table_args__ = (Index('uq_open_alert', 'slot_code', unique=True, mssql_where=text('resolved_at IS NULL'), sqlite_where=text('resolved_at IS NULL')),)

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(60), nullable=False)
    detail = db.Column(db.UnicodeText, nullable=False)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)

class BackupRun(db.Model):
    __tablename__ = 'backup_runs'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.Unicode(255), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    created_at = db.Column(STAMP, nullable=False, default=utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

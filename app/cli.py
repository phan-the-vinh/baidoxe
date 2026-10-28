import os
import click
from datetime import datetime
from sqlalchemy import func, inspect, select
from .extensions import db
from .models import (User, SiteLock, Area, Slot, Rate, ParkingCapacityHistory,
                     LEGACY_SLOT_CODES, TARGET_SLOT_CODES)

def seed_database(username, password, full_name='Quản lý bãi xe'):
    if len(password) < 10:
        raise click.ClickException('Mật khẩu quản lý cần ít nhất 10 ký tự.')
    if not db.session.get(SiteLock, 1):
        db.session.add(SiteLock(id=1, revision=0))
    existing_codes = tuple(db.session.scalars(select(Slot.code).order_by(Slot.code)))
    existing_areas = tuple(db.session.scalars(select(Area.code).order_by(Area.code)))
    if not existing_codes and not existing_areas:
        db.session.add(Area(code='D', name='Khu D', description='24 vị trí cố định, từ D01 đến D24.'))
        db.session.flush()
        db.session.add_all(Slot(code=code, area_code='D', state='EMPTY') for code in TARGET_SLOT_CODES)
    elif not (
        (existing_codes == LEGACY_SLOT_CODES and existing_areas == ('A', 'B'))
        or (existing_codes == TARGET_SLOT_CODES and existing_areas == ('D',))
    ):
        raise click.ClickException('Cấu hình vị trí không phải A01–B10 hoặc D01–D24; không tự ý seed hay sửa dữ liệu.')
    if inspect(db.engine).has_table(ParkingCapacityHistory.__tablename__):
        if not db.session.scalar(select(ParkingCapacityHistory.effective_at).limit(1)):
            baseline = 20 if existing_codes == LEGACY_SLOT_CODES else 24
            db.session.add(ParkingCapacityHistory(
                effective_at=datetime(1900, 1, 1), capacity=baseline,
                reason=f'BASELINE_{baseline}'
            ))
    if not db.session.scalar(select(Rate.id).limit(1)):
        db.session.add(Rate(turn_fee=20000, night_fee=80000, monthly_fee=None))
    if not db.session.scalar(select(User.id).where(User.username == username)):
        user = User(username=username, full_name=full_name, role='ADMIN')
        user.set_password(password)
        db.session.add(user)
    db.session.commit()

def register_cli(app):
    @app.cli.command('init-db')
    def init_db():
        """Tao bang va du lieu cau hinh. Khong tao luot gui/gia lap AI."""
        password = os.getenv('ADMIN_PASSWORD', '')
        if len(password) < 10:
            raise click.ClickException('Điền ADMIN_PASSWORD (ít nhất 10 ký tự) trong .env trước.')
        tables = set(inspect(db.engine).get_table_names())
        if not tables:
            db.create_all()
        elif not {'areas', 'slots', 'visits', 'payments', 'audit_logs'}.issubset(tables):
            raise click.ClickException('Database đã có schema không đầy đủ; không tự động tạo hoặc sửa bảng.')
        seed_database(os.getenv('ADMIN_USERNAME', 'admin').lower(), password,
                      os.getenv('ADMIN_FULL_NAME', 'Quản lý bãi xe'))
        count = db.session.scalar(select(func.count()).select_from(Slot))
        click.echo(f'Đã kiểm tra cấu hình {count} ô từ database. Không tạo dữ liệu hoạt động giả.')

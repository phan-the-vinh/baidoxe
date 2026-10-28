from datetime import datetime, time, timedelta, timezone
from sqlalchemy import func, inspect, select, or_
from ..extensions import db
from ..models import Slot, Visit, Payment, ParkingCapacityHistory, utcnow
from .common import VN, today, parse_date, local_time, BusinessError

def date_range(start=None, end=None):
    first = parse_date(start) if start else today()
    last = parse_date(end) if end else first
    if last < first or (last-first).days > 365:
        raise BusinessError('Chọn khoảng thời gian hợp lệ, tối đa 366 ngày.')
    a = datetime.combine(first, time.min, VN).astimezone(timezone.utc).replace(tzinfo=None)
    b = datetime.combine(last + timedelta(days=1), time.min, VN).astimezone(timezone.utc).replace(tzinfo=None)
    return first, last, a, b

def snapshot():
    slots = list(db.session.scalars(select(Slot).order_by(Slot.code)))
    occupied = sum(s.state == 'OCCUPIED' for s in slots)
    return {'capacity': len(slots), 'occupied': occupied, 'empty': len(slots)-occupied,
            'available': sum(s.state == 'EMPTY' and not s.needs_review for s in slots),
            'review': sum(s.needs_review for s in slots),
            'occupancy_percent': round(occupied / len(slots) * 100, 1) if slots else 0}

def capacity_history():
    """Return ordered UTC-naive capacity events, with a safe legacy fallback."""
    if inspect(db.engine).has_table(ParkingCapacityHistory.__tablename__):
        rows = list(db.session.execute(select(
            ParkingCapacityHistory.effective_at,
            ParkingCapacityHistory.capacity,
        ).order_by(ParkingCapacityHistory.effective_at)))
        if rows:
            return [(row.effective_at, row.capacity) for row in rows]
    capacity = db.session.scalar(select(func.count()).select_from(Slot)) or 0
    return [(datetime(1900, 1, 1), capacity)]

def capacity_at(moment, events=None):
    events = events or capacity_history()
    applicable = [capacity for effective_at, capacity in events if effective_at <= moment]
    return applicable[-1] if applicable else (events[0][1] if events else 0)

def capacity_seconds(start, stop, events=None):
    if stop <= start:
        return 0.0
    events = events or capacity_history()
    boundaries = [start] + [effective_at for effective_at, _ in events if start < effective_at < stop] + [stop]
    return sum(
        capacity_at(segment_start, events) * (segment_stop - segment_start).total_seconds()
        for segment_start, segment_stop in zip(boundaries, boundaries[1:])
    )

def statistics(start=None, end=None):
    first, last, a, b = date_range(start, end)
    now = utcnow()
    effective_end = min(b, now)
    visits = list(db.session.scalars(select(Visit).where(
        Visit.entered_at < b, or_(Visit.exited_at.is_(None), Visit.exited_at >= a))))
    payments = list(db.session.scalars(select(Payment).where(Payment.paid_at >= a, Payment.paid_at < b)))
    arrivals = [v for v in visits if a <= v.entered_at < b]
    departures = [v for v in visits if v.exited_at and a <= v.exited_at < b]
    hours = [{'hour': i, 'arrivals': 0, 'departures': 0, 'occupancy_percent': 0} for i in range(24)]
    for v in arrivals:
        hours[local_time(v.entered_at).hour]['arrivals'] += 1
    for v in departures:
        hours[local_time(v.exited_at).hour]['departures'] += 1
    occupied_seconds = [0.0] * 24
    capacity_second_totals = [0.0] * 24
    capacity_events = capacity_history()
    tick = a
    while tick < effective_end:
        stop = min(tick + timedelta(hours=1), effective_end)
        hour = local_time(tick).hour
        capacity_second_totals[hour] += capacity_seconds(tick, stop, capacity_events)
        occupied_seconds[hour] += sum(max(0, (min(v.exited_at or effective_end, stop) - max(v.entered_at, tick)).total_seconds()) for v in visits)
        tick = stop
    for row in hours:
        h = row['hour']
        row['occupancy_percent'] = round(occupied_seconds[h]/capacity_second_totals[h]*100, 2) if capacity_second_totals[h] else 0
    capacity_total = sum(capacity_second_totals)
    return {'start': str(first), 'end': str(last), 'timezone': 'Asia/Ho_Chi_Minh',
            'arrivals': len(arrivals), 'departures': len(departures),
            'turn_revenue': sum(p.amount for p in payments if p.kind == 'TURN'),
            'monthly_revenue': sum(p.amount for p in payments if p.kind == 'MONTH'),
            'revenue': sum(p.amount for p in payments), 'hours': hours,
            'occupancy_percent': round(sum(occupied_seconds)/capacity_total*100, 2) if capacity_total else 0,
            'occupancy_basis': 'Thời gian các lượt gửi chiếm ô / tổng sức chứa hiệu lực theo từng thời kỳ.',
            'has_data': bool(visits or payments), 'current': snapshot(),
            'as_of': local_time(now).isoformat()}

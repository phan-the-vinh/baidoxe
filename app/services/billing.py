"""Phi theo dac ta V1.1; quy tac chi tiet trong docs/NGHIEP_VU.md."""
from datetime import datetime, time, timedelta
from math import ceil
from .common import local_time, BusinessError

def calculate_fee(entered_at, exited_at, rate, monthly=False):
    if exited_at < entered_at:
        raise BusinessError('Thời gian ra không thể trước thời gian vào.')
    if monthly:
        return {'total': 0, 'turns': 0, 'nights': [], 'regular_seconds': 0,
                'note': 'Vé tháng có hiệu lực tại thời điểm vào; miễn phí lượt này.'}
    start, end = local_time(entered_at), local_time(exited_at)
    remaining = (end - start).total_seconds()
    nights = []
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        night_start = datetime.combine(day, time(22), start.tzinfo)
        night_end = datetime.combine(day + timedelta(days=1), time(8), start.tzinfo)
        overlap = max(0.0, (min(end, night_end) - max(start, night_start)).total_seconds())
        if overlap >= 5 * 3600:
            nights.append({'date': day.isoformat(), 'seconds': overlap, 'fee': rate.night_fee})
            remaining -= overlap
        day += timedelta(days=1)
    remaining = max(0, remaining)
    turns = ceil(remaining / 7200)
    if not nights:
        turns = max(1, turns)
    return {'total': int(turns * rate.turn_fee + len(nights) * rate.night_fee),
            'turns': turns, 'turn_fee': rate.turn_fee, 'nights': nights,
            'night_fee': rate.night_fee, 'regular_seconds': remaining,
            'note': 'Phần ngoài các đêm đủ điều kiện được cộng lại, làm tròn lên mỗi 2 giờ.'}

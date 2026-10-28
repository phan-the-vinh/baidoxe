from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from app.services.billing import calculate_fee
from app.services.common import VN,BusinessError

def dt(value):return datetime.fromisoformat(value).replace(tzinfo=VN).astimezone(timezone.utc).replace(tzinfo=None)

@pytest.mark.parametrize('start,end,expected',[
    ('2026-09-01 09:00:00','2026-09-01 09:00:00',20000),
    ('2026-09-01 09:00:00','2026-09-01 09:00:01',20000),
    ('2026-09-01 09:00:00','2026-09-01 11:00:00',20000),
    ('2026-09-01 09:00:00','2026-09-01 11:00:01',40000),
    ('2026-09-01 09:00:00','2026-09-01 13:00:00',40000),
    ('2026-09-01 22:00:00','2026-09-02 02:59:59',60000),
    ('2026-09-01 22:00:00','2026-09-02 03:00:00',80000),
    ('2026-09-01 22:00:00','2026-09-02 08:00:00',80000),
    ('2026-09-02 03:00:00','2026-09-02 08:00:00',80000),
    ('2026-09-02 03:00:01','2026-09-02 08:00:00',60000),
    ('2026-09-01 21:00:00','2026-09-02 09:00:00',100000),
    ('2026-09-01 21:00:00','2026-09-02 09:00:01',120000),
    ('2026-09-01 22:00:00','2026-09-03 08:00:00',300000),
    ('2026-09-01 08:00:00','2026-09-01 22:00:00',140000),
])
def test_prices_at_boundaries(start,end,expected):
    assert calculate_fee(dt(start),dt(end),SimpleNamespace(turn_fee=20000,night_fee=80000))['total']==expected

def test_monthly_and_invalid_time():
    rate=SimpleNamespace(turn_fee=20000,night_fee=80000)
    assert calculate_fee(dt('2026-01-01'),dt('2026-01-10'),rate,True)['total']==0
    with pytest.raises(BusinessError):calculate_fee(dt('2026-01-02'),dt('2026-01-01'),rate)

import json
import uuid
from sqlalchemy import select
from ..extensions import db
from ..models import Slot, Visit, Rate, MonthlyPass, PassPeriod, Payment, Alert, Observation, utcnow
from .common import (BusinessError, audit, write_lock, normalize_plate,
                     confirmed, local_time, field, parse_date, integer)
from .billing import calculate_fee

def current_rate():
    rate = db.session.scalar(select(Rate).order_by(Rate.id.desc()).limit(1))
    if not rate:
        raise BusinessError('Chưa có bảng giá. Hãy khởi tạo CSDL.', 409)
    return rate


def clear_alert(slot, actor, note):
    for alert in db.session.scalars(select(Alert).where(Alert.slot_code == slot.code, Alert.resolved_at.is_(None))):
        alert.resolved_at, alert.resolved_by, alert.resolution = utcnow(), actor, note
    slot.needs_review = False

def check_in(data, actor):
    confirmed(data, 'plate_confirmed')
    confirmed(data, 'slot_confirmed')
    plate = normalize_plate(data.get('plate'))
    write_lock()
    if db.session.scalar(select(Visit.id).where(Visit.plate == plate, Visit.exited_at.is_(None))):
        raise BusinessError('Xe đã có lượt Đang gửi. Không thể tạo lượt trùng.', 409)
    slot = db.session.get(Slot, str(data.get('slot_code', '')))
    if not slot or slot.state != 'EMPTY' or slot.needs_review:
        raise BusinessError('Ô này không còn trống hoặc đang cần kiểm tra. Hãy chọn lại.', 409)
    if db.session.scalar(select(Visit.id).where(Visit.slot_code == slot.code, Visit.exited_at.is_(None))):
        raise BusinessError('Vị trí đã được gán cho lượt gửi khác.', 409)
    now = utcnow()
    visit = Visit(ticket_code='V' + uuid.uuid4().hex[:16].upper(), plate=plate,
        slot_code=slot.code, entered_at=now, ticket_type='TURN',
        rate_id=current_rate().id, created_by=actor)
    db.session.add(visit)
    slot.state, slot.changed_at = 'OCCUPIED', now
    audit(actor, 'CHECK_IN', {'ticket': visit.ticket_code, 'plate': plate, 'slot': slot.code})
    db.session.flush()
    return visit

def quote(visit, at=None):
    if visit.exited_at:
        raise BusinessError('Lượt gửi đã hoàn tất.', 409)
    at = at or utcnow()
    return calculate_fee(visit.entered_at, at, visit.rate, visit.ticket_type == 'MONTH')

def check_out(visit_id, data, actor):
    confirmed(data, 'vehicle_confirmed')
    write_lock()
    visit = db.session.get(Visit, visit_id, populate_existing=True)
    if not visit:
        raise BusinessError('Không tìm thấy lượt gửi.', 404)
    now = utcnow()
    fees = quote(visit, now)
    if visit.ticket_type == 'TURN':
        confirmed(data, 'paid_confirmed')
    if integer(data, 'quoted_amount', maximum=2_000_000_000) != fees['total']:
        raise BusinessError('Phí đã thay đổi theo thời gian. Hãy tính lại phí và xác nhận số tiền mới.', 409)
    visit.exited_at, visit.exited_by = now, actor
    visit.amount, visit.fee_breakdown = fees['total'], json.dumps(fees, ensure_ascii=False)
    slot = db.session.get(Slot, visit.slot_code)
    slot.state, slot.changed_at = 'EMPTY', now
    # Canh bao cu chi duoc dong bang xac nhan kiem tra; khong am tham dong o day.
    if visit.ticket_type == 'TURN':
        db.session.add(Payment(visit_id=visit.id, amount=fees['total'], kind='TURN', cashier_id=actor, paid_at=now))
    audit(actor, 'CHECK_OUT', {'ticket': visit.ticket_code, 'amount': fees['total']})
    return visit

def move_visit(visit_id, data, actor):
    confirmed(data, 'confirmed')
    note = field(data, 'reason', 500)
    write_lock()
    visit = db.session.get(Visit, visit_id, populate_existing=True)
    if not visit or visit.exited_at:
        raise BusinessError('Chỉ đổi vị trí của xe đang gửi.', 409)
    target = db.session.get(Slot, data.get('slot_code'))
    if not target or target.code == visit.slot_code:
        raise BusinessError('Chọn một vị trí khác trong bãi.')
    if db.session.scalar(select(Visit.id).where(Visit.slot_code == target.code, Visit.exited_at.is_(None))):
        raise BusinessError('Ô đích đang gắn với xe khác.', 409)
    old = db.session.get(Slot, visit.slot_code)
    old.state, target.state = 'EMPTY', 'OCCUPIED'
    old.changed_at = target.changed_at = utcnow()
    clear_alert(old, actor, note)
    clear_alert(target, actor, note)
    audit(actor, 'MOVE_VISIT', {'ticket': visit.ticket_code, 'from': old.code, 'to': target.code, 'reason': note})
    visit.slot_code = target.code

def set_slot(code, data, actor):
    confirmed(data, 'confirmed')
    note = field(data, 'reason', 500)
    state = data.get('state')
    if state not in ('EMPTY', 'OCCUPIED'):
        raise BusinessError('Trạng thái phải là EMPTY hoặc OCCUPIED.')
    write_lock()
    slot = db.session.get(Slot, code)
    if not slot:
        raise BusinessError('Vị trí không tồn tại.', 404)
    if state == 'EMPTY' and db.session.scalar(select(Visit.id).where(Visit.slot_code == code, Visit.exited_at.is_(None))):
        raise BusinessError('Ô còn lượt Đang gửi. Hãy chuyển đúng vị trí hoặc hoàn tất xe ra trước.', 409)
    slot.state, slot.changed_at = state, utcnow()
    clear_alert(slot, actor, note)
    audit(actor, 'CONFIRM_SLOT', {'slot': code, 'state': state, 'reason': note})


def accept_observations(predictions, captured_at, threshold):
    write_lock()
    for item in predictions:
        slot = db.session.get(Slot, item['slot'])
        stale = captured_at < slot.changed_at or (slot.ai_at and captured_at < slot.ai_at)
        reason = ''
        if stale:
            reason = 'Khung hình cũ hơn thay đổi dữ liệu; bỏ qua.'
        elif item['confidence'] < threshold:
            reason = 'Độ tin cậy thấp; cần kiểm tra thực tế.'
        elif item['state'] != slot.state:
            reason = 'Kết quả AI khác trạng thái đã xác nhận.'
        else:
            reason = 'AI khớp trạng thái đã xác nhận.'
        accepted = not stale and item['confidence'] >= threshold and item['state'] == slot.state
        db.session.add(Observation(slot_code=slot.code, captured_at=captured_at,
            state=item['state'], confidence=item['confidence'], accepted=accepted, reason=reason))
        if not stale:
            slot.ai_state, slot.ai_confidence, slot.ai_at = item['state'], item['confidence'], captured_at
            if not accepted:
                slot.needs_review = True
                alert = db.session.scalar(select(Alert).where(Alert.slot_code == slot.code, Alert.resolved_at.is_(None)))
                if alert:
                    alert.reason = reason
                else:
                    db.session.add(Alert(slot_code=slot.code, reason=reason))
        item['accepted'], item['reason'] = accepted, reason
    return predictions

from sqlalchemy import select

from ..extensions import db
from ..models import Area, Slot, LEGACY_SLOT_CODES, TARGET_SLOT_CODES


def current_layout():
    """Describe the persisted layout without creating or changing any rows."""
    codes = tuple(db.session.scalars(select(Slot.code).order_by(Slot.code)))
    areas = tuple(db.session.scalars(select(Area.code).order_by(Area.code)))
    if codes == LEGACY_SLOT_CODES and areas == ('A', 'B'):
        return {
            'name': 'LEGACY_AB20',
            'supported': True,
            'capacity': 20,
            'slot_codes': codes,
            'area_codes': areas,
            'legacy_cnn_write_enabled': True,
        }
    if codes == TARGET_SLOT_CODES and areas == ('D',):
        return {
            'name': 'D24',
            'supported': True,
            'capacity': 24,
            'slot_codes': codes,
            'area_codes': areas,
            'legacy_cnn_write_enabled': False,
        }
    return {
        'name': 'UNSUPPORTED',
        'supported': False,
        'capacity': len(codes),
        'slot_codes': codes,
        'area_codes': areas,
        'legacy_cnn_write_enabled': False,
    }

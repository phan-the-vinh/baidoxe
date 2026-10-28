import pytest

from app.services.plate_ocr import normalize_ocr_plate


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30A 12345", "30A-12345"),
        ("30A\n12345", "30A-12345"),
        (" 30A-123.45 ", "30A-12345"),
        ("PARKING LEVEL TWO", None),
        ("30A 123", None),
        ("", None),
    ],
)
def test_normalize_ocr_plate_does_not_invent_characters(raw, expected):
    assert normalize_ocr_plate(raw) == expected

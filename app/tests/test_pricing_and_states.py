"""Unit tests — pricing units + formal states (no live API)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.conversation_states import normalize_state, next_state_after_facts, FORMAL_STATES
from app.services.agent_tools import _calc, _unit_mode, _validate_args


def test_banner_sqft_feet():
    # 10x15 ft * 250 * 2 = 75000
    assert _calc(250, "sqft", 10, 15, 2, "ft") == 75000.0


def test_banner_not_inches_on_ft():
    assert _calc(250, "sqft", 10, 15, 2, "ft") != 521.0


def test_sticker_inches_div_144():
    # 2x3 in * 200 * 270 / 144
    expected = round((2 * 3 * 200 * 270) / 144.0, 2)
    assert _calc(270, "sqft", 2, 3, 200, "in") == expected


def test_piece_pricing():
    assert _calc(3500, "piece", None, None, 2, "ft") == 7000.0


def test_unit_mode():
    assert _unit_mode("per sq ft") == "sqft"
    assert _unit_mode("per piece") == "piece"


def test_validate_quantity():
    assert _validate_args("calculate_service_price", {"quantity": 0}) is not None
    assert _validate_args("calculate_service_price", {"quantity": 5}) is None


def test_states():
    assert normalize_state("await_payment") == "awaiting_payment"
    assert next_state_after_facts({"awaiting_file": "design"}, "open") == "awaiting_file"
    assert "ready_for_human" in FORMAL_STATES


if __name__ == "__main__":
    test_banner_sqft_feet()
    test_banner_not_inches_on_ft()
    test_sticker_inches_div_144()
    test_piece_pricing()
    test_unit_mode()
    test_validate_quantity()
    test_states()
    print("ALL TESTS PASSED")

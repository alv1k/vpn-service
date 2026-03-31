"""Тесты для bot_xui/tariffs.py — структура тарифов."""


def test_tariffs_not_empty():
    from bot_xui.tariffs import TARIFFS
    assert len(TARIFFS) > 0


def test_tariffs_required_fields():
    from bot_xui.tariffs import TARIFFS
    required = {"name", "price", "period", "device_limit", "is_test"}
    for tid, t in TARIFFS.items():
        for field in required:
            assert field in t, f"Tariff {tid} missing {field}"


def test_tariffs_prices_non_negative():
    from bot_xui.tariffs import TARIFFS
    for tid, t in TARIFFS.items():
        assert t["price"] >= 0, f"Tariff {tid} has negative price"


def test_test_tariff_is_free():
    from bot_xui.tariffs import TARIFFS
    test_tariffs = {k: v for k, v in TARIFFS.items() if v.get("is_test")}
    assert len(test_tariffs) > 0
    for tid, t in test_tariffs.items():
        assert t["price"] == 0, f"Test tariff {tid} should be free"


def test_tariff_ids_are_strings():
    from bot_xui.tariffs import TARIFFS
    for tid in TARIFFS:
        assert isinstance(tid, str)


def test_device_limits_positive():
    from bot_xui.tariffs import TARIFFS
    for tid, t in TARIFFS.items():
        assert t["device_limit"] > 0, f"Tariff {tid} has non-positive device_limit"


def test_paid_tariffs_have_days():
    from bot_xui.tariffs import TARIFFS
    for tid, t in TARIFFS.items():
        if not t.get("is_test") and t["price"] > 0:
            assert t.get("days", 0) > 0, f"Paid tariff {tid} missing days"


def test_tariff_periods_not_empty():
    from bot_xui.tariffs import TARIFFS
    for tid, t in TARIFFS.items():
        assert len(t["period"]) > 0

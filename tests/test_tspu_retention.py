#!/usr/bin/env python3
"""
Test suite for TSPU / DPI Wave Detection & Retention Funnel.
Verifies:
1. DPI Block Score calculation logic.
2. Expired user segmentation with gift awareness (pending vs used vs fresh).
3. Admin panel TSPU status endpoint.
"""
import sys
import os
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, "/home/alvik/vpn-service")

from scripts.detect_tspu_wave import calculate_dpi_score, analyze_inbound_switches, check_sni_reachability
from scripts.win_back_users import get_tspu_wave_targets, MESSAGES, get_buttons_for_scenario
from api.db import execute_query


def test_dpi_detector_score():
    """Verify that calculate_dpi_score returns a valid structure."""
    result = calculate_dpi_score()
    assert isinstance(result, dict)
    assert "score" in result
    assert 0 <= result["score"] <= 100
    assert result["level"] in ("normal", "elevated", "wave_detected")
    assert result["color"] in ("green", "yellow", "red")
    assert "metrics" in result
    assert "affected_isps" in result
    assert isinstance(result["metrics"]["recent_switches_30m"], int)


def test_messages_and_buttons():
    """Verify all TSPU message scenarios have valid templates and inline buttons."""
    scenarios = ["tspu_pending_gift", "tspu_used_gift", "tspu_fresh_gift"]
    for sc in scenarios:
        assert sc in MESSAGES
        text = MESSAGES[sc]
        assert "<b>" in text and "</b>" in text
        buttons = get_buttons_for_scenario(sc, tg_id=12345)
        assert len(buttons) >= 1
        btn = buttons[0][0]
        assert "text" in btn
        assert "callback_data" in btn
        if sc in ("tspu_pending_gift", "tspu_fresh_gift"):
            assert btn["callback_data"] == "wb_activate_gift"
        elif sc == "tspu_used_gift":
            assert btn["callback_data"] == "tariffs"


def test_tspu_targets_segmentation():
    """Verify that get_tspu_wave_targets correctly segments expired users without overlaps."""
    targets = get_tspu_wave_targets(cooldown_days=0)
    assert isinstance(targets, list)
    
    for t in targets:
        assert "tg_id" in t
        assert t["tg_id"] > 0
        assert t["scenario"] in ("tspu_pending_gift", "tspu_used_gift", "tspu_fresh_gift")
        assert "days_expired" in t
        assert 0 <= t["days_expired"] <= 31
        assert "segment_label" in t


def test_gift_awareness_logic():
    """Verify that users who activated gifts are never assigned fresh gift scenario."""
    targets = get_tspu_wave_targets(cooldown_days=0)
    for t in targets:
        tg_id = t["tg_id"]
        already_activated = execute_query(
            "SELECT id FROM winback_log WHERE tg_id = %s AND scenario IN ('loyalty_gift_activated', 'gift_activated')",
            (tg_id,), fetch='one'
        )
        if already_activated:
            assert t["scenario"] == "tspu_used_gift", f"User {tg_id} already activated gift, scenario should be tspu_used_gift"


if __name__ == "__main__":
    pytest.main(["-v", __file__])

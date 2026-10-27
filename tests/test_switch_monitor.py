"""Unit tests for inbound_switch_monitor.py"""
import json
import pytest
from pathlib import Path
from scripts.inbound_switch_monitor import (
    resolve_isp,
    parse_inbound_from_tag,
    SwitchTracker,
    process_log_line,
)


def test_resolve_isp():
    assert resolve_isp("178.176.75.100") == "MegaFon"
    assert resolve_isp("178.178.214.50") == "MTS"
    assert resolve_isp("91.78.242.10") == "Beeline"
    assert resolve_isp("94.245.146.10") == "Rostelecom (YKT)"
    assert resolve_isp("127.0.0.1") == "Localhost"


def test_parse_inbound_from_tag():
    assert "Hysteria2 Turbo" in parse_inbound_from_tag("inbound-4443")
    assert "Reality Vision" in parse_inbound_from_tag("inbound-7443")
    assert "xHTTP Armor" in parse_inbound_from_tag("in-48745-tcp")
    assert "xHTTP Stream" in parse_inbound_from_tag("in-47447-tcp")


def test_switch_tracker_records_switches(tmp_path):
    state_file = tmp_path / "test_switches.json"
    tracker = SwitchTracker(state_file=state_file)

    # 1st event on Hysteria2
    tracker.process_event("2026/09/18 01:00:00", "178.178.214.50", "inbound-4443", "user1@test")
    assert tracker.total_switches == 0
    assert tracker.user_states["user1@test"]["isp"] == "MTS"

    # 2nd event on Reality Vision -> SWITCH!
    tracker.process_event("2026/09/18 01:00:10", "178.178.214.50", "inbound-7443", "user1@test")
    assert tracker.total_switches == 1
    assert tracker.user_switch_counts["user1@test"] == 1

    # Check report output
    report = tracker.generate_report()
    assert "Hysteria2 Turbo (4443)" in report
    assert "Reality Vision (7443)" in report
    assert "user1@test" in report


def test_process_log_line(tmp_path):
    state_file = tmp_path / "test_switches2.json"
    tracker = SwitchTracker(state_file=state_file)

    line1 = "2026/09/18 04:00:00.123456 from 178.176.75.10:1234 accepted tcp:google.com:443 [in-47447-tcp >> direct] email: user_mega"
    line2 = "2026/09/18 04:00:15.123456 from 178.176.75.10:1234 accepted tcp:google.com:443 [inbound-4443 >> direct] email: user_mega"

    process_log_line(line1, tracker)
    assert tracker.total_switches == 0
    process_log_line(line2, tracker)
    assert tracker.total_switches == 1


def test_admin_inbound_switches_route(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    from admin.routes import api_inbound_switches

    # Mock admin session requirement
    monkeypatch.setattr("admin.routes._require_admin_session", lambda req: None)

    req = MagicMock()
    import asyncio
    res = asyncio.run(api_inbound_switches(req))
    assert isinstance(res, dict)
    assert "total_switches" in res
    assert "total_roaming" in res
    assert "user_states" in res
    assert "switch_matrix" in res
    assert "isp_switches" in res
    assert "recent_events" in res


def test_multidevice_switch_filtering(tmp_path):
    """Test that interleaved packets from multiple devices of the same user are not treated as false switches."""
    state_file = tmp_path / "test_multidevice.json"
    tracker = SwitchTracker(state_file=state_file)

    # Device A (phone on MTS) connects to xHTTP Stream
    tracker.process_event("2026/09/18 01:00:00", "178.178.214.50", "in-47447-tcp", "user_multi@test")
    # Device B (PC on Yakutsk broadband) connects to Hysteria2 Turbo
    tracker.process_event("2026/09/18 01:00:05", "185.26.100.34", "inbound-4443", "user_multi@test")

    # Interleaved packets: both devices continue sending on their respective protocols
    tracker.process_event("2026/09/18 01:00:10", "178.178.214.50", "in-47447-tcp", "user_multi@test")
    tracker.process_event("2026/09/18 01:00:15", "185.26.100.34", "inbound-4443", "user_multi@test")

    # Neither device changed protocols, so total_switches must be 0!
    assert tracker.total_switches == 0

    # Now Device A actually switches from xHTTP Stream to Reality Vision on the same IP
    tracker.process_event("2026/09/18 01:00:20", "178.178.214.50", "inbound-7443", "user_multi@test")
    assert tracker.total_switches == 1


def test_threat_sentinel_blocked_alert(tmp_path, monkeypatch):
    """Test that repeated blocked requests trigger Threat Sentinel alert."""
    state_file = tmp_path / "test_threat_sentinel.json"
    tracker = SwitchTracker(state_file=state_file)

    alerts = []
    monkeypatch.setattr(tracker, "send_telegram_alert", lambda msg: alerts.append(msg))

    # Send 14 blocked events (below threshold of 15)
    for i in range(14):
        tracker.process_blocked_event("2026/10/26 06:00:00", "178.178.214.50", "tcp:ooni.org:443", "user_attacker@test")

    assert len(alerts) == 0

    # 15th event triggers alert
    tracker.process_blocked_event("2026/10/26 06:00:01", "178.178.214.50", "tcp:ooni.org:443", "user_attacker@test")
    assert len(alerts) == 1
    assert "Threat Sentinel" in alerts[0]
    assert "user_attacker@test" in alerts[0]


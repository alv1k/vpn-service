"""Unit tests for inbound_switch_monitor.py"""
import json
import pytest
from pathlib import Path
from scripts.inbound_switch_monitor import (
    resolve_isp,
    parse_inbound_from_tag,
    is_connectivity_check,
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


def test_threat_sentinel_ignores_private_lan(tmp_path, monkeypatch):
    """Test that blocked requests to RFC 1918 private subnets are ignored by Threat Sentinel."""
    from scripts.inbound_switch_monitor import is_private_target

    assert is_private_target("tcp:192.168.1.105:5001") is True
    assert is_private_target("tcp:10.0.0.1:80") is True
    assert is_private_target("tcp:172.16.5.4:443") is True
    assert is_private_target("127.0.0.1:8080") is True
    assert is_private_target("tcp:ooni.org:443") is False
    assert is_private_target("tcp:8.8.8.8:53") is False

    state_file = tmp_path / "test_threat_lan.json"
    tracker = SwitchTracker(state_file=state_file)

    alerts = []
    monkeypatch.setattr(tracker, "send_telegram_alert", lambda msg: alerts.append(msg))

    # Send 25 blocked events targeting a local home IP (e.g. Synology NAS on 192.168.1.105:5001)
    for i in range(25):
        tracker.process_blocked_event("2026/11/03 07:16:00", "185.26.100.34", "tcp:192.168.1.105:5001", "vless_6335998601")

    # Should not trigger any alerts and should not count
    assert len(alerts) == 0
    assert len(tracker.blocked_attempts.get("vless_6335998601", [])) == 0


def test_threat_sentinel_ignores_quic_udp_443(tmp_path, monkeypatch):
    """Test that blocked QUIC requests (udp:*:443) are ignored by Threat Sentinel (legitimate TCP-fallback behavior)."""
    state_file = tmp_path / "test_threat_quic.json"
    tracker = SwitchTracker(state_file=state_file)

    alerts = []
    monkeypatch.setattr(tracker, "send_telegram_alert", lambda msg: alerts.append(msg))

    # Send 25 blocked QUIC events from an active user (e.g. Meta / Apple apps)
    for i in range(25):
        tracker.process_blocked_event("2026/11/08 08:20:00", "193.29.33.45", "udp:157.240.0.63:443", "tiin_50613379")

    # Should not trigger any alerts and should not count
    assert len(alerts) == 0
    assert len(tracker.blocked_attempts.get("tiin_50613379", [])) == 0


def test_is_connectivity_check():
    assert is_connectivity_check("tcp:www.gstatic.com:443") is True
    assert is_connectivity_check("tcp:connectivitycheck.android.com:443") is True
    assert is_connectivity_check("tcp:cp.cloudflare.com:80") is True
    assert is_connectivity_check("tcp:clients3.google.com:443") is True
    assert is_connectivity_check("tcp:google.com:443") is False
    assert is_connectivity_check("tcp:yandex.ru:443") is False


def test_ping_burst_does_not_trigger_dpi_alert(tmp_path, monkeypatch):
    """Verifies that rapid ping checks to connectivity endpoints do not trigger false DPI Sentinel alerts."""
    state_file = tmp_path / "test_ping_filter.json"
    tracker = SwitchTracker(state_file=state_file)

    alerts = []
    monkeypatch.setattr(tracker, "send_telegram_alert", lambda msg: alerts.append(msg))

    # User 1 and User 2 test ping to www.gstatic.com across Reality and xHTTP
    # Line 1: user1 on Reality Standard
    line1 = "2026/11/05 08:31:28.100 from 91.79.243.14:1234 accepted tcp:www.gstatic.com:443 [in-53151-tcp >> direct] email: tiin_web_32"
    # Line 2: user1 pings xHTTP Stream (0s gap, connectivity endpoint)
    line2 = "2026/11/05 08:31:28.150 from 91.79.243.14:1235 accepted tcp:www.gstatic.com:443 [in-47447-tcp >> direct] email: tiin_web_32"
    # Line 3: user2 on Reality Standard
    line3 = "2026/11/05 08:31:39.100 from 91.79.243.14:2234 accepted tcp:www.gstatic.com:443 [in-53151-tcp >> direct] email: tiin_364224373"
    # Line 4: user2 pings xHTTP Stream (0s gap, connectivity endpoint)
    line4 = "2026/11/05 08:31:39.150 from 91.79.243.14:2235 accepted tcp:www.gstatic.com:443 [in-47447-tcp >> direct] email: tiin_364224373"

    for l in (line1, line2, line3, line4):
        process_log_line(l, tracker)

    # Both ping switches should NOT register fallback anomaly events or send alert
    assert len(alerts) == 0
    assert len(tracker.recent_fallback_events) == 0


def test_mass_wave_requires_multiple_ips(tmp_path, monkeypatch):
    """Verifies that multiple user accounts on the same single IP are treated as a single client, not a mass wave."""
    import time
    state_file = tmp_path / "test_mass_wave_ips.json"
    tracker = SwitchTracker(state_file=state_file)

    alerts = []
    monkeypatch.setattr(tracker, "send_telegram_alert", lambda msg: alerts.append(msg))

    now = time.time()
    # 2 users fallback on the SAME IP (e.g. 91.79.243.14 on Beeline) with real browsing target
    tracker.check_anomaly_alert("Beeline", "Reality Standard (53151)", "xHTTP Stream (47447)", "user1", now, client_ip="91.79.243.14", dt=60.0, target="tcp:instagram.com:443")
    tracker.check_anomaly_alert("Beeline", "Reality Standard (53151)", "xHTTP Stream (47447)", "user2", now + 10, client_ip="91.79.243.14", dt=60.0, target="tcp:instagram.com:443")

    # Only 1 unique IP -> should NOT trigger mass wave alert (needs >= 5 events for single IP)
    assert len(alerts) == 0

    # But if a second user from a DIFFERENT IP falls back -> mass wave triggered!
    tracker.check_anomaly_alert("Beeline", "Reality Standard (53151)", "xHTTP Stream (47447)", "user3", now + 20, client_ip="91.78.10.20", dt=60.0, target="tcp:instagram.com:443")
    assert len(alerts) == 1
    assert "DPI Sentinel Alert • Beeline" in alerts[0]




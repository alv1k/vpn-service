import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from api.webhook import app
from api.web_portal import _render_page, _happ_routing_deeplink, _happ_routing_b64

client = TestClient(app)


def test_happ_is_priority_app():
    html = _render_page("TestUser", is_active=True, sub_until=None, sub_url="https://344988.snk.wtf/sub/testtoken")
    assert "Happ" in html
    # Verify Happ is recommended
    assert "Happ', desc: '⚡ Простой и быстрый (Рекомендуется)" in html
    assert "https://ru-server.tiinservice.online/apps/Happ.apk" in html
    assert "https://ru-server.tiinservice.online/apps/setup-Happ.x64.exe" in html
    assert "https://ru-server.tiinservice.online/apps/Happ.macOS.universal.dmg" in html
    assert "https://apps.apple.com/app/happ-proxy-utility/id6504287215" in html


def test_happ_routing_deeplink():
    b64 = _happ_routing_b64()
    assert len(b64) > 10
    deeplink = _happ_routing_deeplink()
    assert deeplink.startswith("happ://routing/add/")


def test_happ_routing_endpoint():
    response = client.get("/happ-routing")
    assert response.status_code == 200
    assert "happ://routing/add/" in response.text


def test_happ_download_endpoints():
    r_apk = client.get("/download/happ/android", follow_redirects=False)
    assert r_apk.status_code in (302, 307)
    assert "Happ.apk" in r_apk.headers["location"]

    r_win = client.get("/download/happ/windows", follow_redirects=False)
    assert r_win.status_code in (302, 307)
    assert "setup-Happ.x64.exe" in r_win.headers["location"]

    r_mac = client.get("/download/happ/macos", follow_redirects=False)
    assert r_mac.status_code in (302, 307)
    assert "Happ.macOS.universal.dmg" in r_mac.headers["location"]

    r_ios = client.get("/download/happ/ios", follow_redirects=False)
    assert r_ios.status_code in (302, 307)
    assert "apps.apple.com" in r_ios.headers["location"]


def test_happ_auto_redirect():
    r_android = client.get("/happ", headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8)"}, follow_redirects=False)
    assert r_android.status_code in (302, 307)
    assert "Happ.apk" in r_android.headers["location"]

    r_ios = client.get("/happ", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}, follow_redirects=False)
    assert r_ios.status_code in (302, 307)
    assert "apps.apple.com" in r_ios.headers["location"]

    r_win = client.get("/happ", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, follow_redirects=False)
    assert r_win.status_code in (302, 307)
    assert "setup-Happ.x64.exe" in r_win.headers["location"]


def test_happ_local_files_exist():
    base = Path("/home/alvik/vpn-service/static/apps")
    assert (base / "Happ.apk").exists()
    assert (base / "setup-Happ.x64.exe").exists()
    assert (base / "Happ.macOS.universal.dmg").exists()
    assert (base / "Happ.apk").stat().st_size > 50 * 1024 * 1024

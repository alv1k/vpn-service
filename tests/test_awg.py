"""Тесты для AmneziaWGClient (api/wireguard.py) — HTTP-клиент к AWG 2.0 Web UI."""
import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def awg_client():
    from api.wireguard import AmneziaWGClient
    return AmneziaWGClient(api_url="http://localhost:51821", password="testpass")


def _make_response(status=200, json_data=None, text_data=None, read_data=None):
    """Create a mock aiohttp response as an async context manager."""
    resp = MagicMock()
    resp.status = status
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    if text_data is not None:
        resp.text = AsyncMock(return_value=text_data)
    if read_data is not None:
        resp.read = AsyncMock(return_value=read_data)
    resp.cookies = MagicMock()
    # async context manager protocol
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(method_responses=None, method_errors=None):
    """
    Create a mock aiohttp.ClientSession.

    method_responses: dict like {"post": response_mock, "get": response_mock}
    method_errors: dict like {"get": SomeException("msg")}
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    for method in ("post", "get", "put", "delete"):
        if method_errors and method in method_errors:
            getattr(session, method).side_effect = method_errors[method]
        elif method_responses and method in method_responses:
            getattr(session, method).return_value = method_responses[method]
        else:
            getattr(session, method).return_value = _make_response(status=404)

    return session


# ─────────────────────────────────────────────
#  Initialization
# ─────────────────────────────────────────────

def test_init_strips_trailing_slash():
    from api.wireguard import AmneziaWGClient
    c = AmneziaWGClient(api_url="http://localhost:51821/", password="p")
    assert c.api_url == "http://localhost:51821"


def test_init_no_session(awg_client):
    assert awg_client.session_cookie is None


# ─────────────────────────────────────────────
#  login
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(awg_client):
    mock_cookie = MagicMock()
    mock_cookie.value = "session-abc"

    resp = _make_response(status=200)
    resp.cookies.get.return_value = mock_cookie

    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.login()

    assert result is True
    assert awg_client.session_cookie == mock_cookie


@pytest.mark.asyncio
async def test_login_failure(awg_client):
    resp = _make_response(status=401)
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.login()

    assert result is False
    assert awg_client.session_cookie is None


@pytest.mark.asyncio
async def test_login_network_error(awg_client):
    session = _make_session(method_errors={"post": aiohttp.ClientError("refused")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.login()

    assert result is False


# ─────────────────────────────────────────────
#  _ensure_logged_in
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_logged_in_calls_login_when_no_cookie(awg_client):
    awg_client.login = AsyncMock()
    await awg_client._ensure_logged_in()
    awg_client.login.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_logged_in_skips_when_has_cookie(awg_client):
    awg_client.session_cookie = MagicMock()
    awg_client.login = AsyncMock()
    await awg_client._ensure_logged_in()
    awg_client.login.assert_not_called()


# ─────────────────────────────────────────────
#  create_client
# ─────────────────────────────────────────────

def _setup_authed_client(awg_client):
    cookie = MagicMock()
    cookie.value = "session-abc"
    awg_client.session_cookie = cookie


@pytest.mark.asyncio
async def test_create_client_success(awg_client):
    _setup_authed_client(awg_client)
    client_data = {"id": "uuid-1", "name": "test_user", "address": "10.10.0.2"}

    resp = _make_response(status=200, json_data=client_data)
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.create_client("test_user")

    assert result == client_data


@pytest.mark.asyncio
async def test_create_client_max_reached(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=500, text_data="Maximum number of clients reached")
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        with pytest.raises(RuntimeError, match="Maximum number of clients"):
            await awg_client.create_client("test_user")


@pytest.mark.asyncio
async def test_create_client_other_error(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=404)
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.create_client("test_user")

    assert result is None


@pytest.mark.asyncio
async def test_create_client_network_error(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"post": aiohttp.ClientError("timeout")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.create_client("test_user")

    assert result is None


# ─────────────────────────────────────────────
#  get_client_config
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_client_config_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=200, text_data="[Interface]\nPrivateKey=abc\n")
    session = _make_session({"get": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.get_client_config("uuid-1")

    assert "[Interface]" in result


@pytest.mark.asyncio
async def test_get_client_config_not_found(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=404)
    session = _make_session({"get": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.get_client_config("bad-id")

    assert result is None


# ─────────────────────────────────────────────
#  list_clients
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_clients_success(awg_client):
    _setup_authed_client(awg_client)
    clients = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]

    resp = _make_response(status=200, json_data=clients)
    session = _make_session({"get": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.list_clients()

    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_clients_network_error(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"get": aiohttp.ClientError("timeout")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.list_clients()

    assert result is None


# ─────────────────────────────────────────────
#  delete_client
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_client_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=204)
    session = _make_session({"delete": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.delete_client("uuid-1")

    assert result is True


@pytest.mark.asyncio
async def test_delete_client_not_found(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=404)
    session = _make_session({"delete": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.delete_client("bad-id")

    assert result is False


@pytest.mark.asyncio
async def test_delete_client_network_error(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"delete": aiohttp.ClientError("fail")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.delete_client("uuid-1")

    assert result is False


# ─────────────────────────────────────────────
#  enable_client / disable_client
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enable_client_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=204)
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.enable_client("uuid-1")

    assert result is True


@pytest.mark.asyncio
async def test_disable_client_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=204)
    session = _make_session({"post": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.disable_client("uuid-1")

    assert result is True


@pytest.mark.asyncio
async def test_enable_client_failure(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"post": aiohttp.ClientError("fail")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.enable_client("uuid-1")

    assert result is False


@pytest.mark.asyncio
async def test_disable_client_failure(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"post": aiohttp.ClientError("fail")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.disable_client("uuid-1")

    assert result is False


# ─────────────────────────────────────────────
#  update_client_name / update_client_address
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_client_name_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=204)
    session = _make_session({"put": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.update_client_name("uuid-1", "new_name")

    assert result is True


@pytest.mark.asyncio
async def test_update_client_address_success(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=204)
    session = _make_session({"put": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.update_client_address("uuid-1", "10.10.0.5")

    assert result is True


@pytest.mark.asyncio
async def test_update_client_address_failure(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=500)
    session = _make_session({"put": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.update_client_address("uuid-1", "10.10.0.5")

    assert result is False


@pytest.mark.asyncio
async def test_update_client_name_network_error(awg_client):
    _setup_authed_client(awg_client)

    session = _make_session(method_errors={"put": aiohttp.ClientError("fail")})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.update_client_name("uuid-1", "new")

    assert result is False


# ─────────────────────────────────────────────
#  get_client_qr_code
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_qr_code_success(awg_client):
    _setup_authed_client(awg_client)
    svg_bytes = b"<svg>qr</svg>"

    resp = _make_response(status=200, read_data=svg_bytes)
    session = _make_session({"get": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.get_client_qr_code("uuid-1")

    assert result == svg_bytes


@pytest.mark.asyncio
async def test_get_qr_code_not_found(awg_client):
    _setup_authed_client(awg_client)

    resp = _make_response(status=404)
    session = _make_session({"get": resp})

    with patch("aiohttp.ClientSession", return_value=session):
        result = await awg_client.get_client_qr_code("bad-id")

    assert result is None

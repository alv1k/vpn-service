"""Тесты для awg_api/awg_manager.py — управление AWG интерфейсом."""
from unittest.mock import patch, MagicMock
import pytest


# ─────────────────────────────────────────────
#  _format_awg_params
# ─────────────────────────────────────────────

def test_format_awg_params_basic():
    from awg_api.awg_manager import _format_awg_params
    srv = {"jc": 5, "jmin": 50, "jmax": 1000, "s1": 89, "s2": 121}
    result = _format_awg_params(srv)
    assert "Jc = 5" in result
    assert "S1 = 89" in result
    assert "S2 = 121" in result


def test_format_awg_params_with_h_values():
    from awg_api.awg_manager import _format_awg_params
    srv = {"jc": 5, "h1": "100000-800000", "h2": 200000, "h3": 300000, "h4": 400000}
    result = _format_awg_params(srv)
    assert "H1 = 100000-800000" in result
    assert "H2 = 200000" in result


def test_format_awg_params_with_i_params():
    from awg_api.awg_manager import _format_awg_params
    srv = {"i1": "<r 110>", "i2": "50"}
    result = _format_awg_params(srv, include_i_params=True)
    assert "I1 = <r 110>" in result
    assert "I2 = 50" in result


def test_format_awg_params_without_i_params():
    from awg_api.awg_manager import _format_awg_params
    srv = {"i1": "<r 110>", "jc": 5}
    result = _format_awg_params(srv, include_i_params=False)
    assert "I1" not in result
    assert "Jc = 5" in result


def test_format_awg_params_empty():
    from awg_api.awg_manager import _format_awg_params
    result = _format_awg_params({})
    assert result == ""


# ─────────────────────────────────────────────
#  generate_keypair
# ─────────────────────────────────────────────

@patch("awg_api.awg_manager.subprocess.run")
@patch("awg_api.awg_manager._run")
def test_generate_keypair(mock_run, mock_subprocess):
    from awg_api.awg_manager import generate_keypair
    mock_run.return_value = MagicMock(stdout="privatekey123\n")
    mock_subprocess.return_value = MagicMock(stdout="publickey456\n")
    priv, pub = generate_keypair()
    assert priv == "privatekey123"
    assert pub == "publickey456"


# ─────────────────────────────────────────────
#  generate_preshared_key
# ─────────────────────────────────────────────

@patch("awg_api.awg_manager._run")
def test_generate_preshared_key(mock_run):
    from awg_api.awg_manager import generate_preshared_key
    mock_run.return_value = MagicMock(stdout="psk789\n")
    assert generate_preshared_key() == "psk789"


# ─────────────────────────────────────────────
#  write_server_conf
# ─────────────────────────────────────────────

@patch("builtins.open", new_callable=MagicMock)
@patch("awg_api.awg_manager.db")
def test_write_server_conf(mock_db, mock_open):
    from awg_api.awg_manager import write_server_conf
    mock_db.get_server_config.return_value = {
        "private_key": "srv_priv", "public_key": "srv_pub",
        "listen_port": 51888,
        "jc": 5, "jmin": 50, "jmax": 1000, "s1": 89, "s2": 121,
        "h1": 0, "h2": 0, "h3": 0, "h4": 0,
        "i1": None, "i2": None, "i3": None, "i4": None, "i5": None,
    }
    mock_db.list_clients.return_value = [
        {
            "name": "client1", "enabled": True,
            "public_key": "cli_pub", "preshared_key": "cli_psk",
            "address": "10.10.0.2",
        },
        {
            "name": "disabled", "enabled": False,
            "public_key": "dis_pub", "preshared_key": "dis_psk",
            "address": "10.10.0.3",
        },
    ]

    write_server_conf()

    handle = mock_open().__enter__()
    written = handle.write.call_args[0][0]
    assert "srv_priv" in written
    assert "cli_pub" in written  # enabled client
    assert "dis_pub" not in written  # disabled client excluded


@patch("awg_api.awg_manager.db")
def test_write_server_conf_no_config(mock_db):
    from awg_api.awg_manager import write_server_conf
    mock_db.get_server_config.return_value = None
    with pytest.raises(RuntimeError, match="No server config"):
        write_server_conf()


# ─────────────────────────────────────────────
#  generate_client_conf
# ─────────────────────────────────────────────

@patch("awg_api.awg_manager.db")
def test_generate_client_conf(mock_db):
    from awg_api.awg_manager import generate_client_conf
    mock_db.get_client.return_value = {
        "private_key": "cli_priv", "address": "10.10.0.5",
        "preshared_key": "cli_psk",
    }
    mock_db.get_server_config.return_value = {
        "public_key": "srv_pub", "listen_port": 51888,
        "jc": 5, "jmin": 50, "jmax": 1000, "s1": 89, "s2": 121,
        "h1": 0, "h2": 0, "h3": 0, "h4": 0,
        "i1": "<r 110>", "i2": None, "i3": None, "i4": None, "i5": None,
    }

    conf = generate_client_conf("client-uuid")
    assert "cli_priv" in conf
    assert "srv_pub" in conf
    assert "10.10.0.5/32" in conf
    assert "I1" not in conf  # i_params excluded for client


@patch("awg_api.awg_manager.db")
def test_generate_client_conf_not_found(mock_db):
    from awg_api.awg_manager import generate_client_conf
    mock_db.get_client.return_value = None
    assert generate_client_conf("bad-id") is None


@patch("awg_api.awg_manager.db")
def test_generate_client_conf_no_server(mock_db):
    from awg_api.awg_manager import generate_client_conf
    mock_db.get_client.return_value = {"private_key": "k", "address": "a", "preshared_key": "p"}
    mock_db.get_server_config.return_value = None
    assert generate_client_conf("id") is None


# ─────────────────────────────────────────────
#  interface management
# ─────────────────────────────────────────────

@patch("awg_api.awg_manager._run")
def test_interface_up(mock_run):
    from awg_api.awg_manager import interface_up
    interface_up()
    mock_run.assert_called_once()
    assert "up" in str(mock_run.call_args)


@patch("awg_api.awg_manager._run")
def test_interface_down(mock_run):
    from awg_api.awg_manager import interface_down
    interface_down()
    mock_run.assert_called_once()
    assert "down" in str(mock_run.call_args)


@patch("awg_api.awg_manager._run")
def test_is_interface_up_true(mock_run):
    from awg_api.awg_manager import is_interface_up
    mock_run.return_value = MagicMock(returncode=0)
    assert is_interface_up() is True


@patch("awg_api.awg_manager._run")
def test_is_interface_up_false(mock_run):
    from awg_api.awg_manager import is_interface_up
    mock_run.return_value = MagicMock(returncode=1)
    assert is_interface_up() is False


# ─────────────────────────────────────────────
#  reload_interface
# ─────────────────────────────────────────────

@patch("awg_api.awg_manager.subprocess.run")
@patch("awg_api.awg_manager._run")
def test_reload_interface(mock_run, mock_subprocess):
    from awg_api.awg_manager import reload_interface
    mock_subprocess.return_value = MagicMock(stdout="[Interface]\nPrivateKey=xxx\n")
    reload_interface()
    # awg syncconf should be called
    assert any("syncconf" in str(c) for c in mock_run.call_args_list)

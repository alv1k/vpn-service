"""Тесты для awg_api/main.py — session management."""
import time
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
import pytest


def test_valid_session_passes():
    """Valid, non-expired session should not raise."""
    from awg_api.main import _check_session_from_request, _sessions

    token = "test-token-valid"
    _sessions[token] = time.time()  # just created

    request = MagicMock()
    request.cookies.get.return_value = token

    # Should not raise
    _check_session_from_request(request)

    # Cleanup
    _sessions.pop(token, None)


def test_missing_cookie_raises_401():
    """Missing cookie should raise 401."""
    from awg_api.main import _check_session_from_request

    request = MagicMock()
    request.cookies.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        _check_session_from_request(request)
    assert exc_info.value.status_code == 401


def test_unknown_token_raises_401():
    """Token not in session store should raise 401."""
    from awg_api.main import _check_session_from_request

    request = MagicMock()
    request.cookies.get.return_value = "nonexistent-token"

    with pytest.raises(HTTPException) as exc_info:
        _check_session_from_request(request)
    assert exc_info.value.status_code == 401


def test_expired_session_raises_401():
    """Session older than SESSION_MAX_AGE should raise 401 and be deleted."""
    from awg_api.main import _check_session_from_request, _sessions, SESSION_MAX_AGE

    token = "test-token-expired"
    _sessions[token] = time.time() - SESSION_MAX_AGE - 100  # expired 100s ago

    request = MagicMock()
    request.cookies.get.return_value = token

    with pytest.raises(HTTPException) as exc_info:
        _check_session_from_request(request)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()

    # Token should be removed from store
    assert token not in _sessions

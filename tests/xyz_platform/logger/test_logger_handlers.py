"""Tests for xyz_platform.logger.handlers — LogstashHandler TCP emit."""

import json
import logging
import socket
from unittest.mock import MagicMock, patch

import pytest

from xyz_platform.logger.handlers import LogstashHandler


def _make_record(message: str = "test event", level: int = logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="test_logger_handlers.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    return record


class TestLogstashHandlerEmit:
    def test_sends_json_terminated_with_newline(self):
        handler = LogstashHandler(host="localhost", port=5000)
        # Use a plain JSON formatter so the message is serialisable
        handler.setFormatter(logging.Formatter('{"message": "%(message)s"}'))

        mock_socket = MagicMock()
        with patch("socket.socket", return_value=mock_socket):
            handler._socket = mock_socket
            handler.emit(_make_record("hello"))

        call_args = mock_socket.sendall.call_args[0][0]
        payload = call_args.decode("utf-8")
        assert payload.endswith("\n")
        # The part before the newline must be valid JSON
        json.loads(payload.strip())

    def test_closes_socket_on_send_error(self):
        handler = LogstashHandler(host="localhost", port=5000)
        handler.setFormatter(logging.Formatter("%(message)s"))

        mock_socket = MagicMock()
        mock_socket.sendall.side_effect = OSError("broken pipe")

        handler._socket = mock_socket
        # handleError should be called — patch it to avoid stderr noise
        handler.handleError = MagicMock()
        handler.emit(_make_record("will fail"))

        mock_socket.close.assert_called_once()
        assert handler._socket is None

    def test_connect_sets_socket(self):
        handler = LogstashHandler(host="127.0.0.1", port=5001)
        mock_sock_instance = MagicMock()
        with patch("socket.socket", return_value=mock_sock_instance):
            handler._connect()

        mock_sock_instance.connect.assert_called_once_with(("127.0.0.1", 5001))
        assert handler._socket is mock_sock_instance

    def test_connect_sets_socket_to_none_on_failure(self):
        handler = LogstashHandler(host="127.0.0.1", port=5001)
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect.side_effect = socket.error("refused")
        with patch("socket.socket", return_value=mock_sock_instance):
            with pytest.raises(socket.error):
                handler._connect()

        assert handler._socket is None

    def test_close_shuts_down_socket(self):
        handler = LogstashHandler()
        mock_socket = MagicMock()
        handler._socket = mock_socket
        handler.close()

        mock_socket.close.assert_called_once()
        assert handler._socket is None

    def test_close_is_safe_when_socket_is_none(self):
        handler = LogstashHandler()
        handler._socket = None
        handler.close()  # must not raise

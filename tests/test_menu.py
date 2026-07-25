"""Tests for dcc-mcp-zbrush unified menu actions (PIP-2905)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# _resolve_instance_id
# ══════════════════════════════════════════════════════════════════════════════


class TestResolveInstanceId:
    def test_server_not_running_returns_none(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        with patch("dcc_mcp_zbrush.server.get_server", return_value=None):
            assert _resolve_instance_id() is None

    def test_server_instance_id_attribute(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        srv = MagicMock()
        srv.instance_id = "abc-123-def"
        with patch("dcc_mcp_zbrush.server.get_server", return_value=srv):
            assert _resolve_instance_id() == "abc-123-def"

    def test_server_config_instance_id(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        srv = MagicMock()
        del srv.instance_id
        srv._config = MagicMock()
        srv._config.instance_id = "cfg-456-xyz"
        with patch("dcc_mcp_zbrush.server.get_server", return_value=srv):
            assert _resolve_instance_id() == "cfg-456-xyz"

    def test_server_rust_core_instance_id(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        srv = MagicMock()
        del srv.instance_id
        srv._config = MagicMock()
        del srv._config.instance_id
        srv._server = MagicMock()
        srv._server.instance_id = "core-789-abc"
        with patch("dcc_mcp_zbrush.server.get_server", return_value=srv):
            assert _resolve_instance_id() == "core-789-abc"

    def test_fallback_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        monkeypatch.setenv("DCC_MCP_INSTANCE_ID", "env-id-999")
        with patch("dcc_mcp_zbrush.server.get_server", return_value=None):
            assert _resolve_instance_id() == "env-id-999"

    def test_import_error_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When get_server raises an exception, fall back to env var."""
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        monkeypatch.setenv("DCC_MCP_INSTANCE_ID", "fallback-import-err")
        # get_server throws → caught by except Exception → srv = None → env fallback
        with patch("dcc_mcp_zbrush.server.get_server", side_effect=RuntimeError("not available")):
            result = _resolve_instance_id()
            assert result == "fallback-import-err"


# ══════════════════════════════════════════════════════════════════════════════
# _server_url
# ══════════════════════════════════════════════════════════════════════════════


class TestServerUrl:
    def test_server_not_running_returns_empty(self) -> None:
        from dcc_mcp_zbrush._menu import _server_url

        with patch("dcc_mcp_zbrush.server.get_server", return_value=None):
            assert _server_url() == ""

    def test_server_running_returns_url(self) -> None:
        from dcc_mcp_zbrush._menu import _server_url

        srv = MagicMock()
        srv.mcp_url = "http://127.0.0.1:18765/mcp"
        with patch("dcc_mcp_zbrush.server.get_server", return_value=srv):
            assert _server_url() == "http://127.0.0.1:18765/mcp"


# ══════════════════════════════════════════════════════════════════════════════
# _set_clipboard_text
# ══════════════════════════════════════════════════════════════════════════════


class TestSetClipboardText:
    def test_pyside6_sets_text(self) -> None:
        """Use PySide6 since it's available in the test environment."""
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        mock_app = MagicMock()
        mock_clipboard = MagicMock()
        mock_app.clipboard.return_value = mock_clipboard

        real_pyside6 = pytest.importorskip("PySide6")
        with patch.object(real_pyside6.QtWidgets.QApplication, "instance", return_value=mock_app):
            _set_clipboard_text("test-123")
            mock_clipboard.setText.assert_called_once_with("test-123")

    def test_fallback_to_pyside6_when_pyside2_no_app(self) -> None:
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        mock_app = MagicMock()
        mock_clipboard = MagicMock()
        mock_app.clipboard.return_value = mock_clipboard

        # Simulate: PySide2 exists but no QApplication instance,
        # falls back to PySide6
        with patch.dict("sys.modules", {"PySide2": MagicMock()}):
            import PySide2  # noqa: PLC0415

            PySide2.QtWidgets.QApplication.instance.return_value = None

            real_pyside6 = pytest.importorskip("PySide6")
            with patch.object(real_pyside6.QtWidgets.QApplication, "instance", return_value=mock_app):
                _set_clipboard_text("test-fallback")
                mock_clipboard.setText.assert_called_once_with("test-fallback")

    def test_raises_when_no_binding(self) -> None:
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        with pytest.raises(RuntimeError, match="Unable to access system clipboard"):
            with patch("builtins.__import__", side_effect=ImportError):
                _set_clipboard_text("should-fail")


# ══════════════════════════════════════════════════════════════════════════════
# _is_inside_zbrush
# ══════════════════════════════════════════════════════════════════════════════


class TestIsInsideZbrush:
    def test_false_outside_zbrush(self) -> None:
        from dcc_mcp_zbrush._menu import _is_inside_zbrush

        assert _is_inside_zbrush() is False


# ══════════════════════════════════════════════════════════════════════════════
# _zbrush_version
# ══════════════════════════════════════════════════════════════════════════════


class TestZbrushVersion:
    def test_unknown_outside_zbrush(self) -> None:
        from dcc_mcp_zbrush._menu import _zbrush_version

        assert _zbrush_version() == "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# copy_instance_id — public API
# ══════════════════════════════════════════════════════════════════════════════


class TestCopyInstanceId:
    def test_no_instance_id_shows_message(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value=None):
            with patch("dcc_mcp_zbrush._menu._show_message") as mock_msg:
                copy_instance_id()
                mock_msg.assert_called_once()
                assert "not available" in mock_msg.call_args[0][1]

    def test_copies_to_clipboard(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        mock_app = MagicMock()
        mock_clipboard = MagicMock()
        mock_app.clipboard.return_value = mock_clipboard

        real_pyside6 = pytest.importorskip("PySide6")

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-123"):
            with patch("dcc_mcp_zbrush._menu._is_inside_zbrush", return_value=True):
                with patch.object(real_pyside6.QtWidgets.QApplication, "instance", return_value=mock_app):
                    copy_instance_id()
                    mock_clipboard.setText.assert_called_once_with("uuid-123")

    def test_clipboard_failure_shows_error(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-456"):
            with patch("dcc_mcp_zbrush._menu._set_clipboard_text", side_effect=RuntimeError("clipboard error")):
                with patch("dcc_mcp_zbrush._menu._show_message") as mock_msg:
                    copy_instance_id()
                    mock_msg.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# show_server_info — public API
# ══════════════════════════════════════════════════════════════════════════════


class TestShowServerInfo:
    def test_shows_info_dialog(self) -> None:
        from dcc_mcp_zbrush._menu import show_server_info

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-abc"):
            with patch("dcc_mcp_zbrush._menu._server_url", return_value="http://127.0.0.1:8765/mcp"):
                with patch("dcc_mcp_zbrush._menu._zbrush_version", return_value="2026.1"):
                    with patch("dcc_mcp_zbrush._menu._show_message") as mock_msg:
                        show_server_info()
                        mock_msg.assert_called_once()
                        msg = mock_msg.call_args[0][1]
                        assert "uuid-abc" in msg
                        assert "ZBrush 2026.1" in msg
                        assert "http://127.0.0.1:8765/mcp" in msg


# ══════════════════════════════════════════════════════════════════════════════
# show_about — public API
# ══════════════════════════════════════════════════════════════════════════════


class TestShowAbout:
    def test_shows_about_dialog(self) -> None:
        from dcc_mcp_zbrush._menu import show_about

        with patch("dcc_mcp_zbrush._menu._zbrush_version", return_value="2026.2"):
            with patch("dcc_mcp_zbrush._menu._show_message") as mock_msg:
                show_about()
                mock_msg.assert_called_once()
                msg = mock_msg.call_args[0][1]
                assert "dcc-mcp-zbrush" in msg
                assert "2026.2" in msg
                assert "github.com/dcc-mcp/dcc-mcp-zbrush" in msg


# ══════════════════════════════════════════════════════════════════════════════
# _show_message
# ══════════════════════════════════════════════════════════════════════════════


class TestShowMessage:
    def test_prints_fallback_outside_zbrush(self, capsys: pytest.CaptureFixture) -> None:
        from dcc_mcp_zbrush._menu import _show_message

        _show_message("Title", "Body text")
        captured = capsys.readouterr()
        assert "Title" in captured.out
        assert "Body text" in captured.out


# ══════════════════════════════════════════════════════════════════════════════
# Module exports
# ══════════════════════════════════════════════════════════════════════════════


class TestMenuExports:
    """Verify menu functions are exported from the public package."""

    def test_copy_instance_id_exported(self) -> None:
        from dcc_mcp_zbrush import copy_instance_id

        assert callable(copy_instance_id)

    def test_show_server_info_exported(self) -> None:
        from dcc_mcp_zbrush import show_server_info

        assert callable(show_server_info)

    def test_show_about_exported(self) -> None:
        from dcc_mcp_zbrush import show_about

        assert callable(show_about)

"""Tests for dcc-mcp-zbrush unified menu actions (PIP-2905)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


class TestResolveInstanceId:
    def test_reads_public_server_property(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        server = MagicMock()
        server.instance_id = "abc-123-def"

        with patch("dcc_mcp_zbrush.server.get_server", return_value=server):
            assert _resolve_instance_id() == "abc-123-def"

    def test_missing_public_property_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        server = MagicMock(spec=[])
        monkeypatch.setenv("DCC_MCP_INSTANCE_ID", "must-not-be-used")

        with patch("dcc_mcp_zbrush.server.get_server", return_value=server):
            assert _resolve_instance_id() is None

    def test_server_not_running_returns_none(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        with patch("dcc_mcp_zbrush.server.get_server", return_value=None):
            assert _resolve_instance_id() is None

    def test_import_error_returns_none(self) -> None:
        from dcc_mcp_zbrush._menu import _resolve_instance_id

        with patch("dcc_mcp_zbrush.server.get_server", side_effect=RuntimeError("not available")):
            assert _resolve_instance_id() is None


class TestServerUrl:
    def test_server_not_running_returns_empty(self) -> None:
        from dcc_mcp_zbrush._menu import _server_url

        with patch("dcc_mcp_zbrush.server.get_server", return_value=None):
            assert _server_url() == ""

    def test_server_running_returns_url(self) -> None:
        from dcc_mcp_zbrush._menu import _server_url

        server = MagicMock()
        server.mcp_url = "http://127.0.0.1:18765/mcp"

        with patch("dcc_mcp_zbrush.server.get_server", return_value=server):
            assert _server_url() == "http://127.0.0.1:18765/mcp"


class TestQtFallbacks:
    def test_pyside2_clipboard_imports_qtwidgets_submodule(self) -> None:
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        widgets = MagicMock()
        app = widgets.QApplication.instance.return_value

        with patch("dcc_mcp_zbrush._menu.importlib.import_module", return_value=widgets) as import_module:
            _set_clipboard_text("qt2")

        import_module.assert_called_once_with("PySide2.QtWidgets")
        app.clipboard.return_value.setText.assert_called_once_with("qt2")

    def test_falls_back_to_pyside6_qtwidgets_submodule(self) -> None:
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        qt2_widgets = MagicMock()
        qt2_widgets.QApplication.instance.return_value = None
        qt6_widgets = MagicMock()
        qt6_app = qt6_widgets.QApplication.instance.return_value

        with patch(
            "dcc_mcp_zbrush._menu.importlib.import_module",
            side_effect=[qt2_widgets, qt6_widgets],
        ) as import_module:
            _set_clipboard_text("qt6")

        assert import_module.call_args_list == [
            call("PySide2.QtWidgets"),
            call("PySide6.QtWidgets"),
        ]
        qt6_app.clipboard.return_value.setText.assert_called_once_with("qt6")

    def test_raises_when_no_binding_is_importable(self) -> None:
        from dcc_mcp_zbrush._menu import _set_clipboard_text

        with patch("dcc_mcp_zbrush._menu.importlib.import_module", side_effect=ImportError):
            with pytest.raises(RuntimeError, match="Unable to access system clipboard"):
                _set_clipboard_text("should-fail")

    def test_native_zbrush_message_is_preferred(self) -> None:
        from dcc_mcp_zbrush._menu import _show_message

        zbc = MagicMock()
        with patch("dcc_mcp_zbrush._menu._zbrush_commands", return_value=zbc):
            with patch("dcc_mcp_zbrush._menu.importlib.import_module") as import_module:
                _show_message("Title", "Body")

        zbc.message_ok.assert_called_once_with("Body", "Title")
        import_module.assert_not_called()

    def test_message_falls_back_to_pyside6(self) -> None:
        from dcc_mcp_zbrush._menu import _show_message

        qt6_widgets = MagicMock()
        with patch("dcc_mcp_zbrush._menu._zbrush_commands", return_value=None):
            with patch(
                "dcc_mcp_zbrush._menu.importlib.import_module",
                side_effect=[ImportError, qt6_widgets],
            ) as import_module:
                _show_message("Title", "Body")

        assert import_module.call_args_list == [
            call("PySide2.QtWidgets"),
            call("PySide6.QtWidgets"),
        ]
        qt6_widgets.QMessageBox.information.assert_called_once_with(None, "Title", "Body")


class TestMenuActions:
    def test_no_instance_id_shows_cli_guidance(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value=None):
            with patch("dcc_mcp_zbrush._menu._show_message") as show_message:
                copy_instance_id()

        assert "dcc-mcp-cli list" in show_message.call_args.args[1]

    def test_copy_uses_clipboard_helper(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-123"):
            with patch("dcc_mcp_zbrush._menu._set_clipboard_text") as set_clipboard:
                copy_instance_id()

        set_clipboard.assert_called_once_with("uuid-123")

    def test_clipboard_failure_shows_error(self) -> None:
        from dcc_mcp_zbrush._menu import copy_instance_id

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-456"):
            with patch("dcc_mcp_zbrush._menu._set_clipboard_text", side_effect=RuntimeError("clipboard error")):
                with patch("dcc_mcp_zbrush._menu._show_message") as show_message:
                    copy_instance_id()

        assert "clipboard error" in show_message.call_args.args[1]

    def test_server_info_includes_runtime_details(self) -> None:
        from dcc_mcp_zbrush._menu import show_server_info

        with patch("dcc_mcp_zbrush._menu._resolve_instance_id", return_value="uuid-abc"):
            with patch("dcc_mcp_zbrush._menu._server_url", return_value="http://127.0.0.1:8765/mcp"):
                with patch("dcc_mcp_zbrush._menu._zbrush_version", return_value="2026.1"):
                    with patch("dcc_mcp_zbrush._menu._show_message") as show_message:
                        show_server_info()

        message = show_message.call_args.args[1]
        assert "uuid-abc" in message
        assert "ZBrush 2026.1" in message
        assert "http://127.0.0.1:8765/mcp" in message

    def test_about_includes_adapter_and_host_versions(self) -> None:
        from dcc_mcp_zbrush._menu import show_about

        with patch("dcc_mcp_zbrush._menu._zbrush_version", return_value="2026.2"):
            with patch("dcc_mcp_zbrush._menu._show_message") as show_message:
                show_about()

        message = show_message.call_args.args[1]
        assert "dcc-mcp-zbrush" in message
        assert "2026.2" in message
        assert "github.com/dcc-mcp/dcc-mcp-zbrush" in message


class TestInstallMenu:
    def test_registers_palette_and_three_buttons(self) -> None:
        from dcc_mcp_zbrush._menu import install_menu

        zbc = MagicMock()
        zbc.exists.return_value = False
        zbc.add_palette.return_value = True
        zbc.add_button.return_value = True

        assert install_menu(zbc) is True
        zbc.add_palette.assert_called_once_with("DCC MCP", docking_bar=1)
        assert [args.args[0] for args in zbc.add_button.call_args_list] == [
            "DCC MCP:Copy Instance ID",
            "DCC MCP:Server Info",
            "DCC MCP:About DCC MCP",
        ]
        assert all(call_args.args[2].__name__.startswith("_on_") for call_args in zbc.add_button.call_args_list)

    def test_sender_callbacks_dispatch_public_actions(self) -> None:
        from dcc_mcp_zbrush import _menu

        with patch.object(_menu, "copy_instance_id") as copy:
            with patch.object(_menu, "show_server_info") as server_info:
                with patch.object(_menu, "show_about") as about:
                    _menu._on_copy_instance_id("DCC MCP:Copy Instance ID")
                    _menu._on_show_server_info("DCC MCP:Server Info")
                    _menu._on_show_about("DCC MCP:About DCC MCP")

        copy.assert_called_once_with()
        server_info.assert_called_once_with()
        about.assert_called_once_with()

    def test_existing_palette_and_buttons_are_idempotent(self) -> None:
        from dcc_mcp_zbrush._menu import install_menu

        zbc = MagicMock()
        zbc.exists.return_value = True

        assert install_menu(zbc) is True
        zbc.add_palette.assert_not_called()
        zbc.add_button.assert_not_called()

    def test_palette_failure_is_reported(self) -> None:
        from dcc_mcp_zbrush._menu import install_menu

        zbc = MagicMock()
        zbc.exists.return_value = False
        zbc.add_palette.return_value = False

        assert install_menu(zbc) is False
        zbc.add_button.assert_not_called()


class TestMenuExports:
    def test_actions_and_installer_are_exported(self) -> None:
        from dcc_mcp_zbrush import copy_instance_id, install_menu, show_about, show_server_info

        assert all(callable(item) for item in (copy_instance_id, show_server_info, show_about, install_menu))

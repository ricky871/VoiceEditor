import argparse
import asyncio

import pytest

import main_gui


def test_create_start_processing_handler_awaits_workflow(monkeypatch):
    captured = {}

    async def fake_start_processing(*args):
        captured["args"] = args

    monkeypatch.setattr(main_gui, "start_processing", fake_start_processing)

    params = [object(), object(), object(), object(), object(), object()]
    handler = main_gui.create_start_processing_handler(*params)

    asyncio.run(handler())

    assert captured["args"] == tuple(params)


def test_create_start_synthesis_handler_awaits_workflow(monkeypatch):
    captured = {}

    async def fake_start_synthesis(*args):
        captured["args"] = args

    monkeypatch.setattr(main_gui, "start_synthesis", fake_start_synthesis)

    params = [object(), object(), object(), object(), object(), object(), object(), object()]
    handler = main_gui.create_start_synthesis_handler(*params)

    asyncio.run(handler())

    assert captured["args"] == tuple(params)


def test_parse_runtime_args_reads_remote_gui_env(monkeypatch):
    monkeypatch.setenv("VOICEEDITOR_GUI_HOST", "0.0.0.0")
    monkeypatch.setenv("VOICEEDITOR_GUI_PORT", "8196")
    monkeypatch.setenv("VOICEEDITOR_GUI_PUBLIC_HOST", "10.245.54.160")
    monkeypatch.setenv("VOICEEDITOR_GUI_PUBLIC_PORT", "8196")
    monkeypatch.setenv("VOICEEDITOR_GUI_SOCKET_IO_TRANSPORTS", "polling,websocket")
    monkeypatch.setenv("VOICEEDITOR_GUI_RECONNECT_TIMEOUT", "60")
    monkeypatch.setenv("VOICEEDITOR_GUI_BINDING_REFRESH_INTERVAL", "0.5")

    args = main_gui.parse_runtime_args([])

    assert args.host == "0.0.0.0"
    assert args.port == 8196
    assert args.public_host == "10.245.54.160"
    assert args.public_port == 8196
    assert args.socket_io_transports == ["polling", "websocket"]
    assert args.reconnect_timeout == 60.0
    assert args.binding_refresh_interval == 0.5


def test_parse_runtime_args_rejects_invalid_transport():
    with pytest.raises(SystemExit):
        main_gui.parse_runtime_args(["--socket-io-transports", "websocket,invalid"])


def test_resolve_public_base_url_prefers_public_host():
    runtime_args = argparse.Namespace(host="0.0.0.0", public_host="10.245.54.160", public_port=None)

    assert main_gui.resolve_public_base_url(runtime_args, 8196) == "http://10.245.54.160:8196"


def test_resolve_public_base_url_falls_back_to_localhost_for_wildcard():
    runtime_args = argparse.Namespace(host="0.0.0.0", public_host="", public_port=None)

    assert main_gui.resolve_public_base_url(runtime_args, 8196) == "http://127.0.0.1:8196"


def test_register_disconnect_cleanup_cancels_all_timers():
    class FakeClient:
        def __init__(self):
            self.handler = None

        def on_disconnect(self, handler):
            self.handler = handler

    class FakeTimer:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    client = FakeClient()
    timers = [FakeTimer(), FakeTimer()]

    main_gui.register_disconnect_cleanup(client, *timers)
    client.handler()

    assert all(timer.cancelled for timer in timers)
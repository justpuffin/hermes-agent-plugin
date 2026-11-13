"""The Puffin plugin for Hermes: the client, the tools, the platform adapter, setup and registration."""
import asyncio
import importlib
import io
import json
import urllib.error

import pytest

import adapter as adapter_module
import puffin_api
import setup_cli
import tools
from conftest import PlatformConfig

AGENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MID = "11111111-2222-3333-4444-555555555555"


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Fake urlopen: records requests and answers from a list of (status, body) or a callable."""

    def __init__(self, answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        status, body = self.answers.pop(0)
        if callable(body):
            body = body(request)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(data))
        return Reply(data)


def client(answers):
    opener = Opener(answers)
    return puffin_api.Puffin("https://api.example.test", "pf_secret", AGENT, opener=opener), opener


# Client ------------------------------------------------------------------------------------------------------------

def test_tool_call_and_errors():
    api, opener = client([(200, {"jsonrpc": "2.0", "id": "x", "result": {"content": [{"type": "text", "text": "[1, 2]"}]}}),
                          (200, {"jsonrpc": "2.0", "id": "x", "result": {"content": [{"type": "text", "text": "Key lacks scope"}], "isError": True}}),
                          (401, {"detail": "Invalid key"})])
    assert api.call_tool("messages", {"limit": 2}) == "[1, 2]"
    sent = json.loads(opener.requests[0].data)
    assert sent["method"] == "tools/call" and sent["params"] == {"name": "messages", "arguments": {"limit": 2}}
    assert opener.requests[0].full_url == "https://api.example.test/mcp" and opener.requests[0].headers["Authorization"] == "Bearer pf_secret"
    with pytest.raises(puffin_api.PuffinError, match="Key lacks scope"):
        api.call_tool("messages", {})
    with pytest.raises(puffin_api.PuffinError, match="Invalid key"):
        api.code_status()
    with pytest.raises(ValueError):
        puffin_api.Puffin("http://api.example.test", "k", AGENT)  # never send the key over plain http
    assert puffin_api.Puffin("http://127.0.0.1:8000", "k", AGENT).url == "http://127.0.0.1:8000"


def test_event_stream_resumes_and_ends_on_error():
    first = (b": keep-alive\n\nid: 7\nevent: message.received\ndata: {\"id\": 7, \"type\": \"message.received\", \"data\": {\"message_id\": \"m\"}}\n\n"
             b"event: reconnect\ndata: {}\n\n")
    second = b"event: error\ndata: {\"status\": 403, \"detail\": \"Key revoked\"}\n\n"
    api, opener = client([(200, first), (200, second)])
    seen = []
    with pytest.raises(puffin_api.PuffinError, match="Key revoked"):
        for event in api.events(sleep=lambda s: None):
            seen.append(event["id"])
    assert seen == [7] and opener.requests[1].headers["Last-event-id"] == "7"
    assert "consumer=hermes" in opener.requests[0].full_url


# Tools -------------------------------------------------------------------------------------------------------------

def test_tools_forward_to_puffin_and_mark_sends():
    registered = {}

    class Ctx:
        def register_tool(self, name, toolset, schema, handler):
            registered[name] = (toolset, schema, handler)

    class Fake:
        def call_tool(self, name, arguments):
            if name == "reply":
                raise puffin_api.PuffinError(403, "Key lacks required scope: email:write")
            return json.dumps({"tool": name, "args": arguments})
    tools.register_tools(Ctx(), client_factory=Fake)
    assert set(registered) == set(tools.tool_names()) and len(registered) == 23
    toolset, schema, handler = registered["puffin_messages"]
    assert toolset == "puffin" and "untrusted" in schema["description"] and schema["parameters"]["type"] == "object"
    assert "approval" in registered["puffin_reply"][1]["description"] and "approval" not in registered["puffin_messages"][1]["description"]
    assert json.loads(handler({"limit": 3})) == {"success": True, "result": {"tool": "messages", "args": {"limit": 3}}}
    failed = json.loads(registered["puffin_reply"][2]({"message_id": MID, "body": "hi", "idempotency_key": "k" * 8}))
    assert failed["success"] is False and "email:write" in failed["error"]


def test_manifest_lists_every_tool():
    text = (tools.pathlib.Path(tools.__file__).with_name("plugin.yaml")).read_text(encoding="utf-8")
    listed = [line.strip()[2:] for line in text.split("provides_tools:")[1].splitlines() if line.strip().startswith("- ")]
    assert listed == tools.tool_names()
    assert "requires_env" not in text  # nothing is asked at install: the setup code fills the key in


# Adapter -----------------------------------------------------------------------------------------------------------

class FakePuffin:
    def __init__(self, owner=True, runtime="hermes", enabled=True):
        self.owner, self.runtime, self.enabled, self.replies, self.acked = owner, runtime, enabled, [], []

    def code_status(self):
        return {"enabled": self.enabled, "runtime": self.runtime, "runtimes": {"hermes": "Hermes"}}

    def code_message(self, message_id):
        return {"id": message_id, "owner": self.owner, "owner_name": "Priya", "channel": "telegram",
                "conversation": "telegram:555", "text": "deploy the fix"}

    def code_reply(self, message_id, text):
        self.replies.append((message_id, text))

    def ack(self, cursor, consumer="hermes"):
        self.acked.append(cursor)

    def events(self, consumer="hermes", stop=None):
        yield {"id": 1, "type": "message.received", "data": {"message_id": MID}}
        yield {"id": 2, "type": "team_task.created", "data": {}}


def test_adapter_delivers_owner_messages_and_replies_in_the_same_chat():
    fake = FakePuffin()
    item = adapter_module.PuffinAdapter(PlatformConfig(), client=fake)

    async def go():
        assert await item.send("telegram:555", "hi") == adapter_module.SendResult(False, None, item.send.__doc__ or
                                                                                 "Nothing to reply to yet: Puffin replies go to a chat the owner has written in")
        assert await item.deliver({"data": {"message_id": MID}}) is True
        event = item.received[0]
        assert event.text == "deploy the fix" and event.source["user_id"] == "puffin-owner" and event.source["chat_id"] == "telegram:555"
        assert await item.deliver({"data": {"message_id": MID}}) is False  # the same message twice: delivered once
        result = await item.send("telegram:555", "Done: deployed.")
        assert result.success and fake.replies == [(MID, "Done: deployed.")]
    asyncio.run(go())


def test_adapter_ignores_others_and_checks_the_switch():
    async def go(fake):
        item = adapter_module.PuffinAdapter(PlatformConfig(), client=fake)
        return item, await item.connect()
    item, ok = asyncio.run(go(FakePuffin(runtime="claude-code")))
    assert ok is False and not item.connected
    item, ok = asyncio.run(go(FakePuffin(enabled=False)))
    assert ok is False
    stranger = adapter_module.PuffinAdapter(PlatformConfig(), client=FakePuffin(owner=False))
    assert asyncio.run(stranger.deliver({"data": {"message_id": MID}})) is False and stranger.received == []


def test_listen_thread_hands_messages_to_hermes_and_acknowledges():
    fake = FakePuffin()

    async def go():
        item = adapter_module.PuffinAdapter(PlatformConfig(), client=fake)
        assert await item.connect() is True and item.connected
        for _ in range(100):
            if len(fake.acked) == 2:
                break
            await asyncio.sleep(0.02)
        await item.disconnect()
        return item
    item = asyncio.run(go())
    assert fake.acked == [1, 2] and [e.text for e in item.received] == ["deploy the fix"]


# Setup and registration --------------------------------------------------------------------------------------------

def test_setup_saves_the_key_privately(tmp_path, capsys):
    opener = Opener([(201, {"api_key": "pf_paired", "identity_id": AGENT, "agent_name": "Ava", "runtime": "hermes",
                            "api_url": "https://api.example.test"}), (404, {"detail": "This setup code is wrong or expired."})])
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nPUFFIN_API_KEY=old\n")
    setup_cli.setup("ABCDE-FGHJK", "https://api.example.test", path=env, opener=opener)
    text = env.read_text()
    assert "OTHER=1" in text and "PUFFIN_API_KEY=pf_paired" in text and "old" not in text and "PUFFIN_ALLOWED_USERS=puffin-owner" in text
    assert json.loads(opener.requests[0].data) == {"code": "ABCDE-FGHJK"}
    out = capsys.readouterr().out
    assert "Connected to Puffin agent Ava" in out and "pf_paired" not in out
    with pytest.raises(puffin_api.PuffinError, match="wrong or expired"):
        setup_cli.setup("ZZZZZ-ZZZZZ", "https://api.example.test", path=env, opener=opener)


def test_register_adds_the_platform_and_the_command(monkeypatch):
    plugin = importlib.import_module("__init__")
    calls = {}

    class Ctx:
        def register_platform(self, **kwargs):
            calls["platform"] = kwargs

        def register_cli_command(self, name, help, setup_fn, handler_fn):
            calls["cli"] = (name, setup_fn, handler_fn)
    plugin.register(Ctx())
    kwargs = calls["platform"]
    assert kwargs["name"] == "puffin" and kwargs["allowed_users_env"] == "PUFFIN_ALLOWED_USERS" and kwargs["allowlist_suppresses_pairing"] is True
    assert kwargs["max_message_length"] == 1400 and calls["cli"][0] == "puffin"
    monkeypatch.delenv("PUFFIN_API_KEY", raising=False)
    assert kwargs["check_fn"]() is False and kwargs["env_enablement_fn"]() is None
    monkeypatch.setenv("PUFFIN_API_KEY", "pf_x")
    monkeypatch.setenv("PUFFIN_IDENTITY_ID", AGENT)
    assert kwargs["check_fn"]() is True and kwargs["env_enablement_fn"]() == {}
    assert isinstance(kwargs["adapter_factory"](PlatformConfig()), adapter_module.PuffinAdapter)

"""A small Puffin client using only the Python standard library, so the plugin needs no extra packages.

- `call_tool`: one Puffin MCP tool call (Puffin's hosted MCP is stateless: one POST per call).
- `code_*`: the "from your phone" calls (an owner's message, a reply in the same chat).
- `events`: Puffin's live event stream (server-sent events), resumed with Last-Event-ID; `ack` marks events done.
- `pair`: exchange a one-time setup code from the agent's page for this agent's key.
The key is only ever sent to the configured Puffin API over HTTPS (http:// is allowed only for 127.0.0.1).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_URL = "https://api.justpuffin.tech"
CONSUMER = "hermes"
USER_AGENT = "puffin-hermes-plugin/0.1.0"


class PuffinError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f"Puffin API {status}: {detail}")
        self.status, self.detail = status, detail


def check_url(url):
    url = (url or DEFAULT_URL).rstrip("/")
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("The Puffin API address must use https:// (http:// only for 127.0.0.1)")
    return url


def settings(environ=None):
    """(api_url, api_key, identity_id) from the environment (Hermes loads ~/.hermes/.env into it)."""
    environ = os.environ if environ is None else environ
    return (check_url(environ.get("PUFFIN_API_URL") or DEFAULT_URL), (environ.get("PUFFIN_API_KEY") or "").strip(),
            (environ.get("PUFFIN_IDENTITY_ID") or "").strip())


class Puffin:
    def __init__(self, api_url, api_key, identity_id, opener=None):
        self.url, self.key, self.identity = check_url(api_url), api_key, identity_id
        self.open = opener or urllib.request.urlopen
        self.path = f"/v1/identities/{identity_id}"

    @classmethod
    def from_env(cls, environ=None):
        url, key, identity = settings(environ)
        if not key or not identity:
            raise PuffinError(0, "Set PUFFIN_API_KEY and PUFFIN_IDENTITY_ID (run: hermes puffin setup --code <CODE>)")
        return cls(url, key, identity)

    def request(self, method, path, data=None, headers=None, timeout=60):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, data=body, method=method, headers={
            "Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": USER_AGENT,
            **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})})
        try:
            with self.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                detail = json.loads(raw or b"{}").get("detail") or raw.decode(errors="replace")
            except ValueError:
                detail = raw.decode(errors="replace")
            raise PuffinError(error.code, str(detail)[:300]) from None
        return json.loads(raw) if raw else None

    # Tools ----------------------------------------------------------------------------------------------------------
    def call_tool(self, name, arguments):
        """One hosted MCP tool call. Returns the tool's text result; raises PuffinError for an error result."""
        reply = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
                                               "params": {"name": name, "arguments": arguments}},
                             headers={"MCP-Protocol-Version": "2025-11-25"})
        if not isinstance(reply, dict):
            raise PuffinError(0, "Unexpected reply from Puffin")
        if reply.get("error"):
            raise PuffinError(400, reply["error"].get("message", "Tool call refused"))
        result = reply.get("result") or {}
        text = "\n".join(part.get("text", "") for part in result.get("content") or [] if isinstance(part, dict))
        if result.get("isError"):
            raise PuffinError(400, text[:500] or "Tool call failed")
        return text

    # From your phone ------------------------------------------------------------------------------------------------
    def code_status(self):
        return self.request("GET", self.path + "/code")

    def code_message(self, message_id):
        return self.request("GET", f"{self.path}/code/messages/{uuid.UUID(str(message_id))}")

    def code_reply(self, message_id, text):
        return self.request("POST", f"{self.path}/code/messages/{uuid.UUID(str(message_id))}/reply", {"text": text})

    def ack(self, cursor, consumer=CONSUMER):
        return self.request("POST", self.path + "/events/ack", {"cursor": cursor, "consumer": consumer})

    def events(self, consumer=CONSUMER, after=None, stop=None, sleep=time.sleep):
        """Live events, forever (until stop() is true): reconnects after the server's window or a dropped connection,
        resuming after the last event yielded. Raises PuffinError when access ends (revoked key, agent turned off)."""
        last, delay = after, 1
        while not (stop and stop()):
            headers = {"Authorization": "Bearer " + self.key, "Accept": "text/event-stream", "User-Agent": USER_AGENT}
            if last is not None:
                headers["Last-Event-ID"] = str(last)
            query = urllib.parse.urlencode({"consumer": consumer})
            request = urllib.request.Request(f"{self.url}{self.path}/events/stream?{query}", headers=headers)
            try:
                with self.open(request, timeout=75) as response:
                    delay = 1
                    for kind, data in frames(response):
                        if stop and stop():
                            return
                        if kind == "error":
                            raise PuffinError(data.get("status", 403), data.get("detail", "Stream access ended"))
                        if kind == "reconnect":
                            break
                        if isinstance(data, dict) and data.get("id") is not None:
                            last = data["id"]
                            yield data
            except urllib.error.HTTPError as error:
                if error.code in {401, 403, 404, 409}:
                    raise PuffinError(error.code, error.read().decode(errors="replace")[:300]) from None
                sleep(delay)
                delay = min(delay * 2, 30)
            except (urllib.error.URLError, OSError, TimeoutError):
                sleep(delay)
                delay = min(delay * 2, 30)


def frames(response):
    """Server-sent events from a byte stream: yields (event type, decoded JSON data). Heartbeat comments are skipped."""
    kind, data = "message", []
    for raw in response:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield kind, json.loads("\n".join(data))
                except ValueError:
                    pass
            kind, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def pair(code, api_url=None, opener=None):
    """Exchange a one-time setup code (agent page > Coding from your phone > Get setup command) for the agent's key."""
    url = check_url(api_url)
    request = urllib.request.Request(url + "/v1/code/pair", data=json.dumps({"code": code.strip()}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise PuffinError(error.code, detail or "Setup code refused") from None

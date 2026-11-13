"""Stand-ins for the parts of Hermes the plugin imports (gateway.*), so the tests run without Hermes installed.
They mirror the documented interfaces: BasePlatformAdapter(config, platform), build_source(...), handle_message(event),
SendResult(success, message_id, error), MessageEvent(text, message_type, source, message_id)."""
import pathlib
import sys
import types
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class Platform(str):
    pass


@dataclass
class PlatformConfig:
    extra: dict = field(default_factory=dict)


@dataclass
class SendResult:
    success: bool
    message_id: str = None
    error: str = None


class MessageType:
    TEXT = "text"


@dataclass
class MessageEvent:
    text: str
    message_type: str
    source: dict
    message_id: str = None


class BasePlatformAdapter:
    def __init__(self, config, platform):
        self.config, self.platform, self.connected, self.received = config, platform, False, []

    def _mark_connected(self):
        self.connected = True

    def _mark_disconnected(self):
        self.connected = False

    def build_source(self, **fields):
        return dict(fields)

    async def handle_message(self, event):
        self.received.append(event)


class MessageDeduplicator:
    def __init__(self, ttl_seconds=600):
        self.seen = set()

    def is_duplicate(self, key):
        if key in self.seen:
            return True
        self.seen.add(key)
        return False


@dataclass
class PlatformEntry:
    name: str = ""
    label: str = ""
    adapter_factory: object = None
    check_fn: object = None
    validate_config: object = None
    required_env: list = None
    install_hint: str = ""
    env_enablement_fn: object = None
    allowed_users_env: str = ""
    allow_all_env: str = ""
    max_message_length: int = 0
    platform_hint: str = ""
    emoji: str = ""
    allowlist_suppresses_pairing: bool = False


module("gateway")
module("gateway.config", Platform=Platform, PlatformConfig=PlatformConfig)
module("gateway.platforms")
module("gateway.platforms.base", BasePlatformAdapter=BasePlatformAdapter, SendResult=SendResult)
module("gateway.platforms.event", MessageEvent=MessageEvent, MessageType=MessageType)
module("gateway.platforms.helpers", MessageDeduplicator=MessageDeduplicator)
module("gateway.platform_registry", PlatformEntry=PlatformEntry)

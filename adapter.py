"""Hermes from your phone: Puffin as a Hermes messaging platform.

You message your Puffin agent from Telegram, Slack, Discord or email. Puffin passes only your own messages (a linked
workspace admin's chat account, or your confirmed email with DMARC passing) to this adapter over one outgoing live
connection: no public address, webhook or tunnel is needed. Hermes answers, and the answer goes back to the same chat.

Hermes's own approvals work as usual: a dangerous command arrives in the chat as a prompt, and your reply ("yes" /
"no") comes back here as your next message. On the agent's page in Puffin, "Coding from your phone" must be on and set
to Hermes, so no other gateway answers the same messages.
"""
import asyncio
import logging
import threading

try:
    from .puffin_api import CONSUMER, Puffin, PuffinError
except ImportError:
    from puffin_api import CONSUMER, Puffin, PuffinError

from gateway.config import Platform, PlatformConfig  # noqa: F401  (PlatformConfig: type of the config argument)
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType

try:
    from gateway.platforms.helpers import MessageDeduplicator
except ImportError:  # older Hermes
    MessageDeduplicator = None

logger = logging.getLogger(__name__)
PLATFORM = "puffin"
RUNTIME = "hermes"
OWNER = "puffin-owner"  # the only sender id this adapter ever reports: Puffin already checked it is the owner
MAX_LENGTH = 1400  # Puffin splits longer chat replies; keep Hermes's chunks inside one post


def sent(success, message_id=None, error=None):
    try:
        return SendResult(success=success, message_id=message_id, error=error)
    except TypeError:  # SendResult without an error field
        return SendResult(success=success, message_id=message_id)


class PuffinAdapter(BasePlatformAdapter):
    def __init__(self, config, client=None):
        super().__init__(config, Platform(PLATFORM))
        self.client = client
        self.replies = {}  # chat id (Puffin conversation) -> the owner's latest message id there, which replies answer
        self.stop_event = threading.Event()
        self.thread = None
        self.loop = None
        self.dedup = MessageDeduplicator(ttl_seconds=600) if MessageDeduplicator else None

    def puffin(self):
        if self.client is None:
            self.client = Puffin.from_env()
        return self.client

    async def connect(self, *, is_reconnect=False):
        try:
            status = await asyncio.to_thread(self.puffin().code_status)
        except (PuffinError, ValueError) as error:
            logger.error("Puffin: %s", error)
            return False
        if not status.get("enabled"):
            logger.error("Puffin: Coding from your phone is off for this agent. Turn it on from the agent's page in Puffin.")
            return False
        if (status.get("runtime") or "claude-code") != RUNTIME:
            logger.error("Puffin: this agent is set to %s, not Hermes. Change it on the agent's page in Puffin.", status.get("runtime"))
            return False
        self.loop = asyncio.get_running_loop()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.listen, name="puffin-events", daemon=True)
        self.thread.start()
        self._mark_connected()
        return True

    async def disconnect(self):
        self.stop_event.set()
        self._mark_disconnected()

    def listen(self):
        """Background thread: Puffin's live events -> Hermes. Each event is acknowledged after Hermes has it."""
        try:
            for event in self.puffin().events(CONSUMER, stop=self.stop_event.is_set):
                if event.get("type") == "message.received":
                    future = asyncio.run_coroutine_threadsafe(self.deliver(event), self.loop)
                    try:
                        future.result(timeout=300)
                    except Exception as error:  # one bad message must not stop the stream
                        logger.warning("Puffin: a message was not delivered to Hermes: %s", error)
                try:
                    self.puffin().ack(event["id"])
                except PuffinError as error:
                    logger.warning("Puffin: could not acknowledge event %s: %s", event.get("id"), error)
        except PuffinError as error:
            logger.error("Puffin: the live connection ended: %s", error)
            if self.loop and not self.stop_event.is_set():
                self.loop.call_soon_threadsafe(self._mark_disconnected)

    async def deliver(self, event):
        message_id = (event.get("data") or {}).get("message_id")
        if not message_id:
            return False
        info = await asyncio.to_thread(self.puffin().code_message, message_id)
        if not info.get("owner"):
            return False  # not the owner: Puffin never lets it start work, and this adapter ignores it too
        if self.dedup and self.dedup.is_duplicate(str(info["id"])):
            return False
        text = (info.get("text") or "").strip()
        if not text:
            return False
        chat_id = info["conversation"]
        self.replies[chat_id] = info["id"]
        source = self.build_source(chat_id=chat_id, chat_name=str(info.get("channel") or "Puffin").capitalize(),
                                   chat_type="dm", user_id=OWNER, user_name=info.get("owner_name") or "Owner")
        await self.handle_message(MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id=str(info["id"])))
        return True

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        target = self.replies.get(chat_id) or reply_to
        if not target:
            return sent(False, error="Nothing to reply to yet: Puffin replies go to a chat the owner has written in")
        try:
            await asyncio.to_thread(self.puffin().code_reply, target, content)
        except (PuffinError, ValueError) as error:
            return sent(False, error=str(error))
        return sent(True, message_id=str(target))

    async def send_typing(self, chat_id, metadata=None):
        return None

    async def get_chat_info(self, chat_id):
        return {"name": "Puffin " + str(chat_id).split(":", 1)[0], "type": "dm"}

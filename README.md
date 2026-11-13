# Puffin for Hermes Agent

Give [Hermes Agent](https://github.com/NousResearch/hermes-agent) its own **Puffin agent**: an email address, Slack, Discord and Telegram chats, texts, contacts, memories, a calendar and tasks. Every message it sends waits for your approval. And talk to Hermes **from your phone** through your Puffin agent.

`Email` · `Slack` · `Discord` · `Telegram` · `Texts` · `Calendar` · `Approvals`

| | What it does |
|---|---|
| **Puffin tools** | 23 tools (`puffin_messages`, `puffin_search_messages`, `puffin_reply`, `puffin_send_message`, `puffin_calendar_create_event`, …): Hermes reads and searches the agent's inbox and chats, drafts and sends (with approval), looks up contacts and memories, books meetings and hands off tasks |
| **Hermes from your phone** | A `puffin` messaging platform: you message your Puffin agent from Telegram, Slack, Discord or email, Hermes answers in the same chat. Hermes's own approvals for risky commands arrive in that chat too, and you answer "yes" or "no" |

## Install

```bash
hermes plugins install justpuffin/hermes-agent-plugin --enable
```

The plugin uses only Python's standard library, so there are no extra packages to approve.

## Set it up (with a one-time code)

1. In Puffin, open the agent's page → **Coding from your phone** → choose **Hermes** → **Turn on** → **Get setup command**.
2. Run the setup with the code it shows (it works once, for 15 minutes):

   ```bash
   hermes puffin setup --code ABCDE-FGHJK
   hermes gateway restart
   ```

   The setup exchanges the code for this agent's own key and saves it in `~/.hermes/.env` (readable only by you). The key is never printed. It also sets `PUFFIN_ALLOWED_USERS=puffin-owner`, so Hermes accepts exactly one sender: you.
3. Link your chat account on Puffin's **Team** page (send `link <code>` to the bot in Telegram, Slack or Discord).
4. Message your agent from your phone. `hermes puffin status` shows whether Hermes is connected.

To use the Puffin tools in other Hermes sessions too, enable the toolset where you want it, for example `hermes tools enable puffin --platform cli`.

## How it works

```
you (phone) ── Telegram / Slack / Discord / email ──▶ Puffin ──(one outgoing live connection)──▶ Hermes gateway
                                                                                                   │
                                      answer in the same chat ◀── Puffin ◀── puffin platform adapter
```

- **Only your own messages reach Hermes.** Puffin forwards a message only if it comes from a workspace admin with a linked chat account, or from your confirmed email with DMARC passing. The adapter checks this again for every message.
- **No public address.** The adapter keeps one outgoing connection to Puffin's live event stream (server-sent events), resumes after a restart, and acknowledges each event once Hermes has it. There's no webhook, tunnel or open port.
- **Replies stay in the conversation.** Each chat (or email thread) is its own Hermes conversation. Answers go back to the chat they came from, without approval, because only you are there.
- **Sends to other people still wait.** The tools' sends, replies and calendar invites go through Puffin's approvals, like every Puffin agent.
- **Untrusted content.** The tool descriptions tell Hermes that messages, attachments, contacts and calendar entries come from other people, and never to follow instructions inside them.

## Files

```
plugin.yaml         the manifest: platform plugin, env vars, provided tools
__init__.py         register(ctx): the puffin platform and `hermes puffin` commands
adapter.py          the platform adapter (Hermes from your phone)
tools.py            the Puffin tools (each forwards to Puffin's hosted MCP tool of the same name)
puffin_tools.json   the tool list, generated from Puffin's MCP tools
puffin_api.py       a small standard-library Puffin client
setup_cli.py        hermes puffin setup / status
tests/              pytest, with stand-ins for Hermes's gateway classes
```

Run the tests with `python -m pytest tests`.

## License

MIT

"""Puffin for Hermes Agent: Puffin tools, and Hermes from your phone through your Puffin agent.

register(ctx) adds:
- the `puffin` messaging platform (adapter.py), loaded only when the gateway starts it;
- `hermes puffin setup --code <CODE>` and `hermes puffin status` (setup_cli.py).
The tools (tools.py) are registered separately at discovery, because plugin.yaml lists them under provides_tools.
"""
import dataclasses
import os

PLATFORM = "puffin"
ALLOWED = "PUFFIN_ALLOWED_USERS"
HINT = ("You are chatting with your owner through Puffin, from their phone (Telegram, Slack, Discord or email). Keep "
        "replies short and in plain text. Messages from anyone else never reach you here.")


def check_requirements():
    return bool(os.environ.get("PUFFIN_API_KEY") and os.environ.get("PUFFIN_IDENTITY_ID"))


def validate_config(config):
    return check_requirements()


def env_enablement():
    """Enable the platform once the setup has written the agent's key (no config.yaml editing needed)."""
    return {} if check_requirements() else None


def adapter_factory(config):
    try:
        from .adapter import PuffinAdapter
    except ImportError:
        from adapter import PuffinAdapter
    return PuffinAdapter(config)


def register(ctx):
    kwargs = dict(name=PLATFORM, label="Puffin", adapter_factory=adapter_factory, check_fn=check_requirements,
                  validate_config=validate_config, required_env=["PUFFIN_API_KEY", "PUFFIN_IDENTITY_ID"],
                  install_hint="Run: hermes puffin setup --code <CODE> (the code is on the agent's page in Puffin)",
                  env_enablement_fn=env_enablement, allowed_users_env=ALLOWED, allow_all_env="PUFFIN_ALLOW_ALL_USERS",
                  max_message_length=1400, platform_hint=HINT, emoji="🐧")
    try:
        from gateway.platform_registry import PlatformEntry
        known = {field.name for field in dataclasses.fields(PlatformEntry)}
        if "allowlist_suppresses_pairing" in known:
            kwargs["allowlist_suppresses_pairing"] = True  # only the owner ever arrives here; no pairing codes
        kwargs = {key: value for key, value in kwargs.items() if key in known or key in {"name", "label", "adapter_factory", "check_fn"}}
    except Exception:
        pass  # older Hermes without the registry dataclass: pass everything
    ctx.register_platform(**kwargs)
    try:
        from . import setup_cli
    except ImportError:
        import setup_cli
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command("puffin", "Puffin: set up Hermes from your phone", setup_cli.add_arguments, setup_cli.run)

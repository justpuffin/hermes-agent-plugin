"""`hermes puffin setup --code <CODE>` and `hermes puffin status`.

setup exchanges the one-time code from the agent's page in Puffin (Coding from your phone > Hermes > Get setup command)
for that agent's key, and saves it with the agent id in Hermes's .env, where only Hermes reads it. The key is never
printed. It also lets exactly one sender in: the owner, as Puffin reports them.
"""
import os
import pathlib

try:
    from .puffin_api import DEFAULT_URL, Puffin, PuffinError, check_url, pair
except ImportError:
    from puffin_api import DEFAULT_URL, Puffin, PuffinError, check_url, pair

OWNER = "puffin-owner"


def add_arguments(parser):
    commands = parser.add_subparsers(dest="puffin_command")
    setup = commands.add_parser("setup", help="Connect Hermes to your Puffin agent with a one-time setup code")
    setup.add_argument("--code", required=True, help="Setup code from the agent's page in Puffin")
    setup.add_argument("--api-url", default=os.environ.get("PUFFIN_API_URL") or DEFAULT_URL, help="Puffin API address")
    commands.add_parser("status", help="Check the Puffin connection")


def env_file():
    home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
    return pathlib.Path(home) / ".env"


def save(values, path=None):
    """Write KEY=value lines into Hermes's .env, replacing existing ones, readable only by you."""
    try:
        from hermes_cli.gateway_setup_wizard import save_env_values
        save_env_values(**values)
        return "Hermes's .env"
    except Exception:
        pass
    path = pathlib.Path(path or env_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in values]
    lines += [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def setup(code, api_url, path=None, opener=None):
    paired = pair(code, check_url(api_url), opener=opener)
    if paired.get("runtime") not in (None, "hermes"):
        print("Note: this agent is set to " + str(paired["runtime"]) + " in Puffin. Choose Hermes on the agent's page, or Hermes won't answer.")
    where = save({"PUFFIN_API_URL": paired.get("api_url") or api_url, "PUFFIN_API_KEY": paired["api_key"],
                  "PUFFIN_IDENTITY_ID": paired["identity_id"], "PUFFIN_ALLOWED_USERS": OWNER}, path)
    print("Connected to Puffin agent " + str(paired.get("agent_name") or paired["identity_id"]) + ". Saved to " + where + ".")
    print("Restart the gateway (hermes gateway restart), link your chat account on Puffin's Team page, then message the agent.")
    return paired


def status(client=None):
    try:
        client = client or Puffin.from_env()
        info = client.code_status()
    except (PuffinError, ValueError) as error:
        print("Not connected: " + str(error))
        return 1
    ok = info.get("enabled") and info.get("runtime") == "hermes"
    print(("ok  " if info.get("enabled") else "FIX ") + "Coding from your phone is " + ("on" if info.get("enabled") else "off") + " for this agent")
    print(("ok  " if info.get("runtime") == "hermes" else "FIX ") + "The agent is set to " + str((info.get("runtimes") or {}).get(info.get("runtime"), info.get("runtime"))))
    print(("ok  " if info.get("connected") else "..  ") + ("Hermes is connected" if info.get("connected") else "Hermes hasn't connected yet (start the gateway: hermes gateway run)"))
    return 0 if ok else 1


def run(args):
    if getattr(args, "puffin_command", None) == "setup":
        try:
            setup(args.code, args.api_url)
        except (PuffinError, ValueError) as error:
            print("Setup failed: " + str(error))
            return 1
        return 0
    if getattr(args, "puffin_command", None) == "status":
        return status()
    print("Use: hermes puffin setup --code <CODE>   or   hermes puffin status")
    return 2

#!/usr/bin/env python3
"""Interactive Claude agent client for Dev Spaces workspaces."""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Optional

GATEWAY_URL = os.environ.get(
    "CLAUDE_GATEWAY_URL", "http://claude-gateway.claude-sandbox.svc:8080"
)
TOKEN_FILE = os.path.expanduser("~/.claude-token")

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.live import Live
    RICH = True
except ImportError:
    RICH = False

console = Console(force_terminal=True, color_system="truecolor") if RICH else None


def _print(text, **kwargs):
    if RICH:
        console.print(text, **kwargs)
    else:
        print(text)


def _load_token() -> Optional[str]:
    try:
        return open(TOKEN_FILE).read().strip()
    except FileNotFoundError:
        return None


def _save_token(token: str):
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)


def register(username: str) -> str:
    data = json.dumps({"username": username}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/token",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    _save_token(result["token"])
    _print(f"[bold green]Registered as {result['username']}[/bold green]" if RICH
           else f"Registered as {result['username']}")
    return result["token"]


def run(prompt: str):
    token = _load_token()
    if not token:
        _print("[red]No token. Run: python client.py register <name>[/red]" if RICH
               else "No token. Run: python client.py register <name>")
        sys.exit(1)

    data = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/run",
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            while True:
                chunk = resp.read(256)
                if not chunk:
                    break
                text = chunk.decode("utf-8")
                if RICH:
                    console.print(text, end="", highlight=False)
                else:
                    print(text, end="", flush=True)
        print()

    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            _print(f"Error ({e.code}): {body.get('error', 'unknown')}")
        except Exception:
            _print(f"Error ({e.code})")
        if e.code == 401:
            _print("Token expired — run: python client.py register <name>")
        sys.exit(1)


def reset():
    token = _load_token()
    if not token:
        _print("No token. Run: python client.py register <name>")
        sys.exit(1)
    req = urllib.request.Request(
        f"{GATEWAY_URL}/reset",
        data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        _print(result.get("message", "History cleared."))
    except urllib.error.HTTPError as e:
        _print(f"Error ({e.code})")
        sys.exit(1)


def repl(username: str):
    """Interactive REPL loop."""
    register(username)
    run("Clone my GitLab repo workshop-python-microservices and list the files")

    if RICH:
        console.print(Panel(
            "[bold]Commands:[/bold]\n"
            "  Type your prompt — press [bold yellow]Enter twice[/bold yellow] to submit\n"
            "  Paste multi-line code freely, then hit Enter twice\n"
            "  [bold yellow]/reset[/bold yellow]  — clear conversation history\n"
            "  [bold yellow]/quit[/bold yellow]   — exit",
            title="[bold cyan]Claude Agent[/bold cyan]",
            border_style="cyan",
        ))
    else:
        print("Claude Agent — type prompt, press Enter twice to submit. /reset, /quit to exit.")

    while True:
        if RICH:
            console.print("\n[bold green]You:[/bold green] ", end="")
        else:
            print("\nYou: ", end="", flush=True)

        lines = []
        try:
            while True:
                line = input()
                if line == "" and lines:
                    # Blank line after content = submit
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            if RICH:
                console.print("\n[bold yellow]Goodbye![/bold yellow]")
            else:
                print("\nGoodbye!")
            return

        prompt = "\n".join(lines).strip()
        if not prompt:
            continue
        if prompt in ("/quit", "/exit", "exit", "quit"):
            if RICH:
                console.print("[bold yellow]Goodbye![/bold yellow]")
            else:
                print("Goodbye!")
            return
        if prompt == "/reset":
            reset()
            continue

        if RICH:
            console.print("[dim]Thinking...[/dim]")
        run(prompt)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python client.py register <name>   # register and get token")
        print("  python client.py chat <name>        # interactive REPL (recommended)")
        print("  python client.py run <prompt>       # single prompt")
        print("  python client.py reset              # clear history")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "register":
        if len(sys.argv) < 3:
            print("Usage: python client.py register <name>")
            sys.exit(1)
        register(sys.argv[2])
    elif cmd == "chat":
        username = sys.argv[2] if len(sys.argv) > 2 else None
        if username:
            username = username.replace("-devspaces", "")
        if not username:
            print("Usage: python client.py chat <name>")
            sys.exit(1)
        repl(username)
    elif cmd == "run":
        prompt = " ".join(sys.argv[2:])
        if not prompt:
            print("Usage: python client.py run <prompt>")
            sys.exit(1)
        run(prompt)
    elif cmd == "reset":
        reset()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

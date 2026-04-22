"""IT Support Agent — Rich CLI entry point."""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from agent.core import run_agent

load_dotenv()

console = Console()


def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]IT Support Agent[/bold cyan]\n\n"
            "Describe your IT problem in plain language and I'll diagnose\n"
            "and fix it step by step. Type [bold]exit[/bold] or [bold]quit[/bold] to leave.",
            border_style="cyan",
            title="[bold cyan]AI-Powered Service Desk[/bold cyan]",
        )
    )

    history: list[dict] = []

    while True:
        try:
            query = console.input("\n[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Session ended.[/dim]")
            sys.exit(0)

        if not query:
            continue

        if query.lower() in ("exit", "quit"):
            console.print("[dim]Goodbye![/dim]")
            break

        history = run_agent(query, history=history)


if __name__ == "__main__":
    main()

"""Core agentic loop — connects the Rich CLI to the Anthropic API and tool executors."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import anthropic
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from guardrails.classifier import RiskLevel, classify_tool
import tools as tool_module

console = Console()

# ---------------------------------------------------------------------------
# System prompt (stable — cached with cache_control)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert IT support agent running directly on the employee's laptop.
Your job is to diagnose IT problems step-by-step and fix them safely.

RULES:
1. Always start with READ-ONLY diagnostic tools to understand the system state first.
2. Summarise your findings in plain, non-technical language before proposing any fix.
3. For every remediation tool call you must fill the 'explanation' field with a clear,
   jargon-free sentence describing what the action will do and why.
4. Never skip the diagnostic phase — verify the problem before fixing it.
5. After applying a fix, re-run diagnostic tools to confirm the problem is resolved.
6. If you cannot fix the problem, tell the user what to escalate to IT and why.
7. Keep your responses concise and focused on the employee's problem.

AVAILABLE DIAGNOSTIC TOOLS (auto-execute, read-only):
- get_system_info: OS, CPU, RAM, uptime
- get_running_processes: list processes (optional name filter)
- get_network_info: IP addresses, DNS, connectivity test
- get_disk_info: disk space on all drives
- get_services_status: check whether services are running
- read_event_log: recent system/application log entries

AVAILABLE REMEDIATION TOOLS (require employee approval before execution):
- flush_dns_cache: clear the DNS resolver cache
- restart_service: stop and restart a system service
- kill_process: force-quit a hung application
- clear_app_cache: delete temporary cache files for an application
- restart_network_adapter: disable and re-enable a network adapter
- install_package: install a software package via apt/Homebrew/winget

IMPORTANT: Always include a helpful 'explanation' parameter for remediation tools so the
employee understands exactly what will happen before they approve."""

# ---------------------------------------------------------------------------
# Claude API tool schemas
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list[dict] = [
    # ---- DIAGNOSTIC --------------------------------------------------------
    {
        "name": "get_system_info",
        "description": "Read OS version, hostname, CPU usage %, RAM usage %, and system uptime. Read-only — runs automatically.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_running_processes",
        "description": "List running processes sorted by CPU usage (top 25). Optionally filter by process name substring. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional substring to match process name (case-insensitive). E.g. 'outlook', 'chrome'.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_network_info",
        "description": "Read IP addresses, DNS servers, default gateway, and test internet connectivity. Read-only.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_disk_info",
        "description": "Return free and total disk space for all mounted partitions. Read-only.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_services_status",
        "description": "Check whether one or more system services are running or stopped. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of service names to query, e.g. ['Spooler', 'Dhcp', 'Dnscache'].",
                }
            },
            "required": ["service_names"],
        },
    },
    {
        "name": "read_event_log",
        "description": "Read recent Windows Event Log or macOS/Linux system log entries. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_type": {
                    "type": "string",
                    "enum": ["System", "Application", "Security"],
                    "description": "Log name (Windows only). Defaults to 'System'.",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of recent entries to return (default 20).",
                },
            },
            "required": [],
        },
    },
    # ---- REMEDIATION (require approval) ------------------------------------
    {
        "name": "flush_dns_cache",
        "description": "Clear the DNS resolver cache. REQUIRES USER APPROVAL before execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user in the approval prompt. Be specific about why this will help.",
                }
            },
            "required": ["explanation"],
        },
    },
    {
        "name": "restart_service",
        "description": "Stop and restart a named Windows/macOS/Linux service. REQUIRES USER APPROVAL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "Exact service name, e.g. 'Spooler', 'Dnscache', 'com.apple.printing.PrinterProxy'.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user before approval.",
                },
            },
            "required": ["service_name", "explanation"],
        },
    },
    {
        "name": "kill_process",
        "description": "Force-terminate all instances of a named process. REQUIRES USER APPROVAL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "process_name": {
                    "type": "string",
                    "description": "Process name, e.g. 'OUTLOOK.EXE', 'Teams', 'chrome'.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user before approval.",
                },
            },
            "required": ["process_name", "explanation"],
        },
    },
    {
        "name": "clear_app_cache",
        "description": "Delete the cache folder for a specific application. REQUIRES USER APPROVAL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application name, e.g. 'Outlook', 'Teams', 'Chrome', 'Edge'.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user before approval.",
                },
            },
            "required": ["app_name", "explanation"],
        },
    },
    {
        "name": "restart_network_adapter",
        "description": "Disable then re-enable a named network adapter to reset the connection. REQUIRES USER APPROVAL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "adapter_name": {
                    "type": "string",
                    "description": "Adapter name (Windows) or interface name (macOS/Linux, e.g. 'en0', 'eth0').",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user before approval.",
                },
            },
            "required": ["adapter_name", "explanation"],
        },
    },
    {
        "name": "install_package",
        "description": "Install a software package using the system package manager (apt on Linux, Homebrew on macOS, winget on Windows). REQUIRES USER APPROVAL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package_name": {
                    "type": "string",
                    "description": "Package name to install, e.g. 'curl', 'htop', 'nginx', 'python3'.",
                },
                "explanation": {
                    "type": "string",
                    "description": "Plain-language explanation shown to the user before approval.",
                },
            },
            "required": ["package_name", "explanation"],
        },
    },
]

# Map tool name → callable
_TOOL_REGISTRY: dict[str, Any] = {
    "get_system_info": tool_module.get_system_info,
    "get_running_processes": tool_module.get_running_processes,
    "get_network_info": tool_module.get_network_info,
    "get_disk_info": tool_module.get_disk_info,
    "get_services_status": tool_module.get_services_status,
    "read_event_log": tool_module.read_event_log,
    "flush_dns_cache": tool_module.flush_dns_cache,
    "restart_service": tool_module.restart_service,
    "kill_process": tool_module.kill_process,
    "clear_app_cache": tool_module.clear_app_cache,
    "restart_network_adapter": tool_module.restart_network_adapter,
    "install_package": tool_module.install_package,
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_agent(
    user_query: str,
    *,
    on_text: Callable[[str], None] | None = None,
    on_tool: Callable[[str], None] | None = None,
    on_approval: Callable[[str, str], bool] | None = None,
    on_blocked: Callable[[str], None] | None = None,
) -> None:
    """Run one full agentic session for the given user query.

    When callbacks are provided the function is silent (no Rich output) and
    uses the callbacks for all user-facing events — suitable for GUI use.
    When callbacks are omitted it falls back to the original Rich CLI output.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")

    messages: list[dict] = [{"role": "user", "content": user_query}]

    if not on_text:
        console.print()

    while True:
        if on_text:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=[{"type": "text", "text": _SYSTEM_PROMPT,
                          "cache_control": {"type": "ephemeral"}}],
                tools=_TOOL_SCHEMAS,  # type: ignore[arg-type]
                messages=messages,
            )
        else:
            with console.status("[bold blue]Agent is thinking...[/bold blue]", spinner="dots"):
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=[{"type": "text", "text": _SYSTEM_PROMPT,
                              "cache_control": {"type": "ephemeral"}}],
                    tools=_TOOL_SCHEMAS,  # type: ignore[arg-type]
                    messages=messages,
                )

        # ---- Final answer ---------------------------------------------------
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    if on_text:
                        on_text(block.text)
                    else:
                        console.print(Markdown(block.text))
            break

        # ---- Tool use -------------------------------------------------------
        if response.stop_reason == "tool_use":
            tool_results: list[dict] = []

            for block in response.content:
                if hasattr(block, "text") and block.text:
                    if on_text:
                        on_text(block.text)
                    else:
                        console.print(Markdown(block.text))

                if block.type == "tool_use":
                    result = _dispatch_tool(
                        block.name, block.input,
                        on_tool=on_tool,
                        on_approval=on_approval,
                        on_blocked=on_blocked,
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        if not on_text:
            console.print(f"[yellow]Stopped with reason: {response.stop_reason}[/yellow]")
        break


# ---------------------------------------------------------------------------
# Tool dispatcher with guardrails
# ---------------------------------------------------------------------------

def _dispatch_tool(
    name: str,
    inputs: dict,
    *,
    on_tool: Callable[[str], None] | None = None,
    on_approval: Callable[[str, str], bool] | None = None,
    on_blocked: Callable[[str], None] | None = None,
) -> dict:
    """Apply guardrails and execute a tool, returning a result dict."""
    risk = classify_tool(name, inputs.get("command", ""))

    # Blocked — refuse completely
    if risk == RiskLevel.BLOCKED:
        reason = "Command is not permitted by security policy."
        if on_blocked:
            on_blocked(reason)
        else:
            console.print(
                Panel(
                    "[red bold]BLOCKED[/red bold] — This command is not permitted by security policy.",
                    border_style="red",
                    title="Security Block",
                )
            )
        return {"status": "blocked", "reason": reason}

    # Requires approval — explain and ask
    if risk == RiskLevel.REQUIRES_APPROVAL:
        explanation = inputs.get("explanation", f"Run tool '{name}'")
        tech_detail = _format_tech_detail(name, inputs)

        if on_approval:
            approved = on_approval(explanation, tech_detail)
        else:
            console.print(
                Panel(
                    f"[bold yellow]ACTION REQUIRED[/bold yellow]\n\n"
                    f"The agent wants to:\n[cyan]{explanation}[/cyan]\n\n"
                    f"[dim]Technical action: {tech_detail}[/dim]",
                    title="[bold yellow]Security Check[/bold yellow]",
                    border_style="yellow",
                )
            )
            answer = Prompt.ask(
                "[bold]Do you approve this action?[/bold]",
                choices=["y", "n"],
                default="n",
            )
            approved = answer.lower() == "y"

        if not approved:
            if not on_approval:
                console.print("[dim]Action cancelled by user.[/dim]")
            return {"status": "cancelled", "message": "User declined the action."}

    # Execute (safe or approved)
    if on_tool:
        on_tool(name)
    else:
        console.print(f"[dim]  → Executing: {name}[/dim]")

    func = _TOOL_REGISTRY.get(name)
    if func is None:
        return {"status": "error", "error": f"Tool '{name}' not found in registry."}

    try:
        kwargs = {k: v for k, v in inputs.items() if k != "explanation"}
        return func(**kwargs)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _format_tech_detail(name: str, inputs: dict) -> str:
    """Return a short technical summary of what the tool call will do."""
    detail_map = {
        "flush_dns_cache": "ipconfig /flushdns  (or equivalent on macOS/Linux)",
        "restart_service": f"Restart-Service '{inputs.get('service_name', '?')}'",
        "kill_process": f"taskkill /IM '{inputs.get('process_name', '?')}' /F  (or pkill)",
        "clear_app_cache": f"Delete cache folder for {inputs.get('app_name', '?')}",
        "restart_network_adapter": f"Disable/Enable adapter '{inputs.get('adapter_name', '?')}'",
        "install_package": f"apt-get install -y {inputs.get('package_name', '?')}",
    }
    return detail_map.get(name, name)

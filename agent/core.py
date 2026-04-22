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

_SYSTEM_PROMPT = """You are an expert Linux IT support agent (Level 1 & 2).
Your job is to diagnose IT problems step-by-step and fix them safely.

RULES:
1. Always start with READ-ONLY diagnostic tools to understand the system state first.
2. Summarise findings in plain, non-technical language before proposing any fix.
3. For every remediation tool call, fill the 'explanation' field with a clear,
   jargon-free sentence describing what the action will do and why.
4. Never skip the diagnostic phase — verify the problem before fixing it.
5. After applying a fix, re-run diagnostic tools to confirm the problem is resolved.
6. If you cannot fix the problem, tell the user what to escalate to IT and why.
7. Keep responses concise and focused on the user's problem.
8. After clean_disk_space, always run get_disk_info before AND after to confirm space freed.
9. Before manage_firewall_rule, always run check_firewall_status first.
10. If get_failed_logins or get_active_sessions reveals suspicious activity, advise escalation to the security team.

DIAGNOSTIC TOOLS (auto-execute, read-only):
System:   get_system_info, get_running_processes, get_disk_info, get_services_status, read_event_log
Network:  get_network_info, ping_host, dns_lookup, check_open_ports, check_firewall_status, get_vpn_status
Security: get_failed_logins, get_active_sessions, get_last_logins
Disk:     find_large_files, check_filesystem_health
Hardware: get_hardware_info, get_disk_smart_status, get_temperatures, get_io_stats, get_memory_pressure
Packages: get_package_info
Services: get_service_logs, list_cron_jobs

REMEDIATION TOOLS (require employee approval before execution):
- flush_dns_cache, restart_service, kill_process, clear_app_cache, restart_network_adapter
- install_package: install via apt/brew/winget
- clean_disk_space: free disk (targets: apt_cache, old_logs, tmp_files, old_kernels)
- update_all_packages: apt update + upgrade
- fix_broken_packages: apt install -f + dpkg --configure -a
- set_service_autostart: systemctl enable/disable
- manage_firewall_rule: ufw allow/deny/delete a port

IMPORTANT: Always include a helpful 'explanation' parameter for all remediation tools."""

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
    # ---- NETWORK DIAGNOSTICS -----------------------------------------------
    {"name": "ping_host", "description": "Ping a host to test reachability and measure latency. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "host": {"type": "string", "description": "Hostname or IP to ping."},
         "count": {"type": "integer", "description": "Number of pings (default 4)."}}, "required": ["host"]}},
    {"name": "dns_lookup", "description": "Resolve a hostname via DNS (A/AAAA/MX/CNAME). Read-only.",
     "input_schema": {"type": "object", "properties": {
         "hostname": {"type": "string"},
         "record_type": {"type": "string", "enum": ["A","AAAA","MX","CNAME","TXT"], "description": "DNS record type (default A)."}}, "required": ["hostname"]}},
    {"name": "check_open_ports", "description": "Test TCP reachability of specific ports on a host. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "host": {"type": "string"},
         "ports": {"type": "array", "items": {"type": "integer"}, "description": "List of ports to test, e.g. [80, 443, 8080]."}}, "required": ["host", "ports"]}},
    {"name": "check_firewall_status", "description": "Show current UFW/iptables firewall rules. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_vpn_status", "description": "Check whether any VPN tunnel (tun/wg/OpenVPN) is active. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    # ---- SECURITY AUDIT ----------------------------------------------------
    {"name": "get_failed_logins", "description": "Show recent failed SSH/PAM login attempts with source IPs. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "count": {"type": "integer", "description": "Number of recent failures to return (default 20)."}}, "required": []}},
    {"name": "get_active_sessions", "description": "List all currently logged-in users and active TCP connections. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_last_logins", "description": "Show login history for a user or the whole system. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "username": {"type": "string", "description": "Username to filter (leave empty for all users)."},
         "count": {"type": "integer", "description": "Number of entries (default 10)."}}, "required": []}},
    # ---- DISK ---------------------------------------------------------------
    {"name": "find_large_files", "description": "Find the largest files under a path. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Root path to search (default '/')."},
         "min_size_mb": {"type": "integer", "description": "Minimum file size in MB (default 100)."},
         "count": {"type": "integer", "description": "Number of results (default 20)."}}, "required": []}},
    {"name": "check_filesystem_health", "description": "Report filesystem type, last check and error counts. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "device": {"type": "string", "description": "Block device path e.g. /dev/sda1. Leave empty for overview."}}, "required": []}},
    # ---- HARDWARE & PERFORMANCE --------------------------------------------
    {"name": "get_hardware_info", "description": "Return CPU model, RAM DIMMs, USB and PCI devices. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_disk_smart_status", "description": "Read SMART health for a storage drive. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "device": {"type": "string", "description": "Block device e.g. /dev/sda. Leave empty to list drives."}}, "required": []}},
    {"name": "get_temperatures", "description": "Report CPU, GPU and drive temperatures. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_io_stats", "description": "Measure per-disk I/O utilization and CPU I/O wait. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "duration_seconds": {"type": "integer", "description": "Sample window in seconds (default 3)."}}, "required": []}},
    {"name": "get_memory_pressure", "description": "Detailed RAM/swap breakdown, top memory consumers, OOM events. Read-only.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    # ---- PACKAGES ----------------------------------------------------------
    {"name": "get_package_info", "description": "Show installed version, available version and upgrade status of a package. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "package_name": {"type": "string"}}, "required": ["package_name"]}},
    # ---- SERVICES & CRON ---------------------------------------------------
    {"name": "get_service_logs", "description": "Return recent journald log lines for a systemd service. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "service_name": {"type": "string", "description": "systemd service name, e.g. 'nginx', 'sshd'."},
         "count": {"type": "integer", "description": "Number of log lines (default 50)."}}, "required": ["service_name"]}},
    {"name": "list_cron_jobs", "description": "List crontab entries and systemd timers for a user or system-wide. Read-only.",
     "input_schema": {"type": "object", "properties": {
         "username": {"type": "string", "description": "Username to query (leave empty for current user)."}}, "required": []}},
    # ---- NEW REMEDIATION ---------------------------------------------------
    {"name": "clean_disk_space", "description": "Free disk space by cleaning safe targets. REQUIRES USER APPROVAL.",
     "input_schema": {"type": "object", "properties": {
         "targets": {"type": "array", "items": {"type": "string",
             "enum": ["apt_cache", "old_logs", "tmp_files", "old_kernels"]},
             "description": "Which targets to clean."},
         "explanation": {"type": "string"}}, "required": ["targets", "explanation"]}},
    {"name": "update_all_packages", "description": "Run apt update + upgrade to apply all available updates. REQUIRES USER APPROVAL.",
     "input_schema": {"type": "object", "properties": {
         "explanation": {"type": "string"}}, "required": ["explanation"]}},
    {"name": "fix_broken_packages", "description": "Fix broken dpkg/apt state. REQUIRES USER APPROVAL.",
     "input_schema": {"type": "object", "properties": {
         "explanation": {"type": "string"}}, "required": ["explanation"]}},
    {"name": "set_service_autostart", "description": "Enable or disable a service's autostart on boot. REQUIRES USER APPROVAL.",
     "input_schema": {"type": "object", "properties": {
         "service_name": {"type": "string"},
         "enabled": {"type": "boolean", "description": "true = enable, false = disable"},
         "explanation": {"type": "string"}}, "required": ["service_name", "enabled", "explanation"]}},
    {"name": "manage_firewall_rule", "description": "Add or remove a UFW firewall rule. REQUIRES USER APPROVAL.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["allow", "deny", "delete"]},
         "port": {"type": "integer"},
         "protocol": {"type": "string", "enum": ["tcp", "udp"], "description": "Default tcp."},
         "comment": {"type": "string", "description": "Optional rule label."},
         "explanation": {"type": "string"}}, "required": ["action", "port", "explanation"]}},
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
    # New tools
    "ping_host": tool_module.ping_host,
    "dns_lookup": tool_module.dns_lookup,
    "check_open_ports": tool_module.check_open_ports,
    "check_firewall_status": tool_module.check_firewall_status,
    "get_vpn_status": tool_module.get_vpn_status,
    "get_failed_logins": tool_module.get_failed_logins,
    "get_active_sessions": tool_module.get_active_sessions,
    "get_last_logins": tool_module.get_last_logins,
    "find_large_files": tool_module.find_large_files,
    "check_filesystem_health": tool_module.check_filesystem_health,
    "get_hardware_info": tool_module.get_hardware_info,
    "get_disk_smart_status": tool_module.get_disk_smart_status,
    "get_temperatures": tool_module.get_temperatures,
    "get_io_stats": tool_module.get_io_stats,
    "get_memory_pressure": tool_module.get_memory_pressure,
    "get_package_info": tool_module.get_package_info,
    "get_service_logs": tool_module.get_service_logs,
    "list_cron_jobs": tool_module.list_cron_jobs,
    "clean_disk_space": tool_module.clean_disk_space,
    "update_all_packages": tool_module.update_all_packages,
    "fix_broken_packages": tool_module.fix_broken_packages,
    "set_service_autostart": tool_module.set_service_autostart,
    "manage_firewall_rule": tool_module.manage_firewall_rule,
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_agent(
    user_query: str,
    *,
    history: list[dict] | None = None,
    on_text: Callable[[str], None] | None = None,
    on_tool: Callable[[str], None] | None = None,
    on_approval: Callable[[str, str], bool] | None = None,
    on_blocked: Callable[[str], None] | None = None,
) -> list[dict]:
    """Run one full agentic session for the given user query.

    Accepts optional conversation history so the agent remembers previous
    turns. Returns the updated messages list for the caller to persist.

    When callbacks are provided the function is silent (no Rich output) and
    uses the callbacks for all user-facing events — suitable for GUI use.
    When callbacks are omitted it falls back to the original Rich CLI output.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")

    messages: list[dict] = list(history) if history else []
    messages.append({"role": "user", "content": user_query})

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
            messages.append({"role": "assistant", "content": response.content})
            return messages

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
        return messages


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
        "flush_dns_cache":        "systemd-resolve --flush-caches  (or dscacheutil / ipconfig /flushdns)",
        "restart_service":        f"systemctl restart {inputs.get('service_name', '?')}",
        "kill_process":           f"pkill '{inputs.get('process_name', '?')}'  (or taskkill)",
        "clear_app_cache":        f"rm -rf <cache dir for {inputs.get('app_name', '?')}>",
        "restart_network_adapter":f"ip link set {inputs.get('adapter_name', '?')} down/up",
        "install_package":        f"apt-get install -y {inputs.get('package_name', '?')}",
        "clean_disk_space":       f"Clean targets: {inputs.get('targets', [])}",
        "update_all_packages":    "apt-get update && apt-get upgrade -y",
        "fix_broken_packages":    "apt-get install -f && dpkg --configure -a",
        "set_service_autostart":  f"systemctl {'enable' if inputs.get('enabled') else 'disable'} {inputs.get('service_name', '?')}",
        "manage_firewall_rule":   f"ufw {inputs.get('action','?')} {inputs.get('port','?')}/{inputs.get('protocol','tcp')}",
    }
    return detail_map.get(name, name)

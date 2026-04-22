"""L2 escalation tool — creates a persistent ticket and a Markdown report file."""

from __future__ import annotations

import json
from pathlib import Path


def escalate_to_l2(
    problem: str,
    summary: str,
    tried_steps: str,
    priority: str = "medium",
    explanation: str = "",
) -> dict:
    """Create an L2 support ticket when the problem cannot be solved at L1.

    Args:
        problem: Short title describing the problem (max 80 chars).
        summary: Why the problem cannot be solved at L1 level.
        tried_steps: Bullet-point list of all diagnostic/remediation steps attempted.
        priority: Ticket priority — 'low', 'medium', or 'high'.
        explanation: Plain-language explanation shown to the user in the approval dialog.
    """
    from tickets.store import create_ticket
    from tools.diagnostic import get_system_info

    if priority not in ("low", "medium", "high"):
        priority = "medium"

    # Capture current system state
    try:
        sys_info = get_system_info()
    except Exception:
        sys_info = {}

    ticket = create_ticket(
        problem=problem[:80],
        summary=summary,
        tried_steps=tried_steps,
        system_info=sys_info,
        priority=priority,
    )

    # Write human-readable Markdown report for L2 technician
    _write_markdown(ticket, sys_info)

    return {
        "status": "ok",
        "ticket_id": ticket.id,
        "priority": ticket.priority,
        "message": (
            f"Ticket {ticket.id} wurde erstellt und an L2 weitergeleitet. "
            f"Bericht: tickets/{ticket.id}.md"
        ),
    }


def _write_markdown(ticket, sys_info: dict) -> None:
    reports_dir = Path(__file__).parent.parent / "tickets"
    reports_dir.mkdir(exist_ok=True)
    md_path = reports_dir / f"{ticket.id}.md"

    priority_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(ticket.priority, "⚪")

    # Format system info
    if sys_info.get("status") == "ok":
        sys_lines = (
            f"- **OS:** {sys_info.get('os', '?')} {sys_info.get('os_release', '')}\n"
            f"- **Hostname:** {sys_info.get('hostname', '?')}\n"
            f"- **CPU:** {sys_info.get('cpu_usage_percent', '?')}%  "
            f"(Kerne: {sys_info.get('cpu_count', '?')})\n"
            f"- **RAM:** {sys_info.get('ram_usage_percent', '?')}%  "
            f"({sys_info.get('ram_used_gb', '?')} / {sys_info.get('ram_total_gb', '?')} GB)\n"
            f"- **Uptime:** {sys_info.get('uptime', '?')}"
        )
    else:
        sys_lines = "_Systeminfos konnten nicht erfasst werden._"

    md_path.write_text(
        f"# Ticket {ticket.id} — {ticket.problem}\n\n"
        f"| Feld | Wert |\n"
        f"|------|------|\n"
        f"| **Erstellt** | {ticket.created_at} |\n"
        f"| **Status** | {ticket.status} |\n"
        f"| **Priorität** | {priority_emoji} {ticket.priority.upper()} |\n\n"
        f"## Problem-Beschreibung\n\n{ticket.problem}\n\n"
        f"## Zusammenfassung (warum L1 nicht ausreicht)\n\n{ticket.summary}\n\n"
        f"## Bereits probierte Schritte\n\n{ticket.tried_steps}\n\n"
        f"## System-Informationen\n\n{sys_lines}\n\n"
        f"---\n_Automatisch erstellt vom IT Service Desk AI-Agenten._\n",
        encoding="utf-8",
    )

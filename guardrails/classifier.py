import re
from enum import Enum


class RiskLevel(Enum):
    SAFE = "safe"
    REQUIRES_APPROVAL = "requires_approval"
    BLOCKED = "blocked"


# Patterns that are ALWAYS blocked regardless of context
_BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"format\s+[a-z]:",
    r"del\s+/[fs].*c:\\windows",
    r"shutdown\s+/[rs]",
    r"reg\s+delete\s+hklm\\system",
    r":\(\)\{\s*:\|:&\s*\};:",          # fork bomb
    r"dd\s+if=.+of=/dev/[sh]d",
    r"mkfs\.",
    r"wipefs",
]

# Tool names that are always read-only — auto-execute
SAFE_TOOLS: frozenset[str] = frozenset({
    "get_system_info",
    "get_running_processes",
    "get_network_info",
    "get_disk_info",
    "get_services_status",
    "read_event_log",
})

# Tool names that modify system state — require human confirmation
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset({
    "flush_dns_cache",
    "restart_service",
    "kill_process",
    "clear_app_cache",
    "restart_network_adapter",
})


def classify_tool(tool_name: str, command: str = "") -> RiskLevel:
    """Return the risk level for executing a given tool / command string."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return RiskLevel.BLOCKED

    if tool_name in SAFE_TOOLS:
        return RiskLevel.SAFE

    if tool_name in APPROVAL_REQUIRED_TOOLS:
        return RiskLevel.REQUIRES_APPROVAL

    # Fail-safe: unknown tools always need approval
    return RiskLevel.REQUIRES_APPROVAL

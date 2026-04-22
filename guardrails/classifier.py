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
    r"ufw\s+disable",                   # turns off entire firewall
    r"ufw\s+reset",                     # wipes all firewall rules
    r"find\s+/\s+.*-delete",            # arbitrary root-level deletion
]

# Tool names that are always read-only — auto-execute
SAFE_TOOLS: frozenset[str] = frozenset({
    # Original
    "get_system_info",
    "get_running_processes",
    "get_network_info",
    "get_disk_info",
    "get_services_status",
    "read_event_log",
    # Network diagnostics
    "ping_host",
    "dns_lookup",
    "check_open_ports",
    "check_firewall_status",
    "get_vpn_status",
    # Security audit
    "get_failed_logins",
    "get_active_sessions",
    "get_last_logins",
    # Disk
    "find_large_files",
    "check_filesystem_health",
    # Hardware & performance
    "get_hardware_info",
    "get_disk_smart_status",
    "get_temperatures",
    "get_io_stats",
    "get_memory_pressure",
    # Packages
    "get_package_info",
    # Services & cron
    "get_service_logs",
    "list_cron_jobs",
    # Windows diagnostics
    "list_windows_services",
    "get_windows_event_log",
    "get_registry_value",
    "get_windows_network_info",
})

# Tool names that modify system state — require human confirmation
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset({
    # Original
    "flush_dns_cache",
    "restart_service",
    "kill_process",
    "clear_app_cache",
    "restart_network_adapter",
    "install_package",
    # New
    "clean_disk_space",
    "update_all_packages",
    "fix_broken_packages",
    "set_service_autostart",
    "manage_firewall_rule",
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

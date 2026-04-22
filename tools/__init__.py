from tools.diagnostic import (
    get_system_info,
    get_running_processes,
    get_network_info,
    get_disk_info,
    get_services_status,
    read_event_log,
    # Phase 1 — Network
    ping_host,
    dns_lookup,
    check_open_ports,
    check_firewall_status,
    get_vpn_status,
    # Phase 2 — Security
    get_failed_logins,
    get_active_sessions,
    get_last_logins,
    # Phase 3 — Disk
    find_large_files,
    check_filesystem_health,
    # Phase 4 — Hardware & Performance
    get_hardware_info,
    get_disk_smart_status,
    get_temperatures,
    get_io_stats,
    get_memory_pressure,
    # Phase 5 — Packages
    get_package_info,
    # Phase 6 — Services & Cron
    get_service_logs,
    list_cron_jobs,
    # Windows diagnostics
    list_windows_services,
    get_windows_event_log,
    get_registry_value,
    get_windows_network_info,
)
from tools.remediation import (
    flush_dns_cache,
    restart_service,
    kill_process,
    clear_app_cache,
    restart_network_adapter,
    install_package,
    clean_disk_space,
    update_all_packages,
    fix_broken_packages,
    set_service_autostart,
    manage_firewall_rule,
)

__all__ = [
    # Original diagnostic
    "get_system_info", "get_running_processes", "get_network_info",
    "get_disk_info", "get_services_status", "read_event_log",
    # Network
    "ping_host", "dns_lookup", "check_open_ports",
    "check_firewall_status", "get_vpn_status",
    # Security
    "get_failed_logins", "get_active_sessions", "get_last_logins",
    # Disk
    "find_large_files", "check_filesystem_health",
    # Hardware & Performance
    "get_hardware_info", "get_disk_smart_status", "get_temperatures",
    "get_io_stats", "get_memory_pressure",
    # Packages
    "get_package_info",
    # Services & Cron
    "get_service_logs", "list_cron_jobs",
    # Windows diagnostics
    "list_windows_services", "get_windows_event_log",
    "get_registry_value", "get_windows_network_info",
    # Original remediation
    "flush_dns_cache", "restart_service", "kill_process",
    "clear_app_cache", "restart_network_adapter", "install_package",
    # New remediation
    "clean_disk_space", "update_all_packages", "fix_broken_packages",
    "set_service_autostart", "manage_firewall_rule",
]

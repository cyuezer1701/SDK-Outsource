"""Read-only diagnostic tools — executed automatically without user confirmation."""

from __future__ import annotations

import platform
import subprocess
import socket
import datetime
from typing import Optional

import psutil


def get_system_info() -> dict:
    """Return OS version, hostname, CPU usage, RAM usage, and uptime."""
    try:
        boot_time = datetime.datetime.fromtimestamp(psutil.boot_time(), tz=datetime.timezone.utc)
        uptime_seconds = (datetime.datetime.now(datetime.timezone.utc) - boot_time).total_seconds()
        uptime_hours = int(uptime_seconds // 3600)
        uptime_minutes = int((uptime_seconds % 3600) // 60)

        vm = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=1)

        return {
            "status": "ok",
            "os": platform.system(),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "hostname": socket.gethostname(),
            "cpu_count": psutil.cpu_count(logical=True),
            "cpu_usage_percent": cpu_percent,
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_used_gb": round(vm.used / (1024**3), 2),
            "ram_available_gb": round(vm.available / (1024**3), 2),
            "ram_usage_percent": vm.percent,
            "uptime": f"{uptime_hours}h {uptime_minutes}m",
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_running_processes(filter: Optional[str] = None) -> dict:
    """Return the top 25 processes sorted by CPU usage.

    Args:
        filter: Optional substring to match against process name (case-insensitive).
    """
    try:
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = proc.info
                if filter and filter.lower() not in (info.get("name") or "").lower():
                    continue
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": round(info.get("cpu_percent") or 0, 2),
                    "memory_percent": round(info.get("memory_percent") or 0, 2),
                    "status": info.get("status"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
        return {
            "status": "ok",
            "filter": filter,
            "count": len(procs),
            "processes": procs[:25],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_network_info() -> dict:
    """Return IP addresses, DNS servers, default gateway, and basic connectivity test."""
    try:
        result: dict = {"status": "ok", "interfaces": [], "dns_servers": [], "connectivity": {}}

        # Network interfaces
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name in ("AF_INET", "AF_INET6"):
                    result["interfaces"].append({
                        "interface": name,
                        "family": addr.family.name,
                        "address": addr.address,
                        "netmask": addr.netmask,
                    })

        # DNS servers — platform specific
        system = platform.system()
        if system == "Windows":
            dns_raw = _run_cmd(
                ["powershell", "-Command",
                 "Get-DnsClientServerAddress | Select-Object -ExpandProperty ServerAddresses"],
                timeout=10,
            )
            result["dns_servers"] = [line.strip() for line in dns_raw.splitlines() if line.strip()]
        elif system == "Darwin":
            dns_raw = _run_cmd(["scutil", "--dns"], timeout=10)
            result["dns_servers"] = [
                line.split()[-1]
                for line in dns_raw.splitlines()
                if "nameserver" in line.lower()
            ]
        else:
            try:
                with open("/etc/resolv.conf") as f:
                    result["dns_servers"] = [
                        line.split()[1]
                        for line in f
                        if line.startswith("nameserver")
                    ]
            except OSError:
                pass

        # Quick connectivity check
        for host, label in [("8.8.8.8", "google_dns"), ("1.1.1.1", "cloudflare_dns")]:
            try:
                sock = socket.create_connection((host, 53), timeout=3)
                sock.close()
                result["connectivity"][label] = "reachable"
            except OSError:
                result["connectivity"][label] = "unreachable"

        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_disk_info() -> dict:
    """Return free and total space for all mounted disk partitions."""
    try:
        partitions = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024**3), 2),
                    "used_gb": round(usage.used / (1024**3), 2),
                    "free_gb": round(usage.free / (1024**3), 2),
                    "usage_percent": usage.percent,
                })
            except (PermissionError, OSError):
                pass

        return {"status": "ok", "partitions": partitions}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_services_status(service_names: list[str]) -> dict:
    """Return the running/stopped status of named services.

    Args:
        service_names: List of service names to query.
    """
    try:
        system = platform.system()
        results = {}

        if system == "Windows":
            for svc in service_names:
                out = _run_cmd(
                    ["powershell", "-Command",
                     f"(Get-Service -Name '{svc}' -ErrorAction SilentlyContinue).Status"],
                    timeout=10,
                )
                results[svc] = out.strip() or "NotFound"

        elif system == "Darwin":
            for svc in service_names:
                out = _run_cmd(["launchctl", "list", svc], timeout=10)
                results[svc] = "Running" if "PID" in out else "Stopped/NotFound"

        else:  # Linux / other
            for svc in service_names:
                out = _run_cmd(["systemctl", "is-active", svc], timeout=10)
                results[svc] = out.strip().capitalize()

        return {"status": "ok", "services": results}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def read_event_log(log_type: str = "System", count: int = 20) -> dict:
    """Read recent system log entries.

    Args:
        log_type: On Windows: System, Application, or Security.
                  On macOS/Linux: ignored (reads unified/system log).
        count: Number of recent entries to return.
    """
    try:
        system = platform.system()

        if system == "Windows":
            ps_script = (
                f"Get-EventLog -LogName '{log_type}' -Newest {count} | "
                "Select-Object TimeGenerated,EntryType,Source,Message | "
                "ConvertTo-Json -Depth 2"
            )
            raw = _run_cmd(["powershell", "-Command", ps_script], timeout=20)
            return {"status": "ok", "platform": "Windows", "log_type": log_type, "raw": raw}

        elif system == "Darwin":
            raw = _run_cmd(
                ["log", "show", "--last", f"{count}m", "--style", "compact",
                 "--predicate", "eventType == logEvent"],
                timeout=20,
            )
            lines = raw.splitlines()[-count:]
            return {"status": "ok", "platform": "macOS", "entries": lines}

        else:  # Linux
            raw = _run_cmd(
                ["journalctl", "-n", str(count), "--no-pager", "-o", "short"],
                timeout=20,
            )
            return {"status": "ok", "platform": "Linux", "entries": raw.splitlines()}

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _run_cmd(cmd: list[str], timeout: int = 15) -> str:
    """Run a subprocess command and return stdout as a string."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout

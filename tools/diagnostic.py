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


def _sh(cmd: str, timeout: int = 15) -> str:
    """Run a shell command string and return combined stdout+stderr."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    return (result.stdout + result.stderr).strip()


def _trunc(s: str, limit: int = 3000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n[… truncated at {limit} chars]"


# ---------------------------------------------------------------------------
# Phase 1 — Network diagnostics
# ---------------------------------------------------------------------------

def ping_host(host: str, count: int = 4) -> dict:
    """Send ICMP pings to a host and return packet loss + round-trip times."""
    try:
        out = _sh(f"ping -c {count} -W 3 {host}", timeout=30)
        return {"status": "ok", "host": host, "output": out}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def dns_lookup(hostname: str, record_type: str = "A") -> dict:
    """Resolve a hostname using dig/nslookup and return DNS answers."""
    try:
        out = _sh(f"dig +short {record_type} {hostname}", timeout=10)
        if not out:
            out = _sh(f"nslookup {hostname}", timeout=10)
        return {"status": "ok", "hostname": hostname, "record_type": record_type,
                "output": out or "(no result)"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def check_open_ports(host: str, ports: list) -> dict:
    """Test TCP reachability of specific ports on a host."""
    results = {}
    for port in ports:
        try:
            out = _sh(
                f"timeout 3 bash -c 'echo >/dev/tcp/{host}/{port}' && echo open || echo closed",
                timeout=6,
            )
            results[port] = "open" if "open" in out else "closed"
        except Exception:
            results[port] = "timeout"
    return {"status": "ok", "host": host, "ports": results}


def check_firewall_status() -> dict:
    """Return current UFW / iptables firewall rules."""
    try:
        ufw = _sh("ufw status verbose 2>/dev/null || echo 'ufw not available'", timeout=10)
        ipt = _sh("iptables -L INPUT -n --line-numbers 2>/dev/null | head -40", timeout=10)
        return {"status": "ok", "ufw": ufw, "iptables_input": ipt}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_vpn_status() -> dict:
    """Check whether any VPN tunnel interface is active."""
    try:
        interfaces = _sh(
            "ip link show | grep -E 'tun|tap|wg|vpn' || echo 'none'", timeout=8)
        nmcli = _sh(
            "nmcli connection show --active 2>/dev/null | grep -i vpn || echo 'none'",
            timeout=8)
        services = _sh(
            "systemctl list-units --type=service --state=running 2>/dev/null "
            "| grep -iE 'vpn|openvpn|wireguard|wg-quick' || echo 'none'",
            timeout=8)
        return {"status": "ok", "tunnel_interfaces": interfaces,
                "nmcli_vpn": nmcli, "vpn_services": services}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 2 — Security audit
# ---------------------------------------------------------------------------

def get_failed_logins(count: int = 20) -> dict:
    """Return recent failed SSH/PAM login attempts with source IPs."""
    try:
        journal = _sh(
            f"journalctl -u ssh -u sshd -n {count} --no-pager 2>/dev/null "
            f"| grep -iE 'failed|invalid|disconnect' | tail -{count}",
            timeout=15)
        lastb = _sh(f"lastb -n {count} 2>/dev/null | head -{count+2}", timeout=10)
        return {"status": "ok", "journal_failures": journal or "(none)",
                "lastb": lastb or "(none)"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_active_sessions() -> dict:
    """List all currently logged-in users and active TCP connections."""
    try:
        who_out = _sh("who", timeout=5)
        w_out   = _sh("w --no-header", timeout=5)
        conns   = _sh(
            "ss -tnp state established 2>/dev/null | head -30", timeout=8)
        return {"status": "ok", "who": who_out or "(nobody)",
                "w": w_out or "(nobody)", "connections": conns}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_last_logins(username: str = "", count: int = 10) -> dict:
    """Show login history for a user or the whole system."""
    try:
        last_out = _sh(f"last -n {count} {username}".strip(), timeout=10)
        lastlog  = _sh(
            f"lastlog -u {username} 2>/dev/null" if username else
            "lastlog 2>/dev/null | head -30",
            timeout=10)
        return {"status": "ok", "last": last_out, "lastlog": lastlog}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 3 — Disk & storage diagnostics
# ---------------------------------------------------------------------------

def find_large_files(path: str = "/", min_size_mb: int = 100, count: int = 20) -> dict:
    """Find the largest files under a given path."""
    try:
        out = _sh(
            f"find {path} -type f -size +{min_size_mb}M "
            f"-printf '%s\\t%p\\n' 2>/dev/null | sort -rn | head -{count}",
            timeout=60)
        if not out:
            out = f"No files larger than {min_size_mb} MB found under {path}."
        return {"status": "ok", "path": path, "min_size_mb": min_size_mb,
                "output": _trunc(out)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def check_filesystem_health(device: str = "") -> dict:
    """Report filesystem type, last check, and error counts without unmounting."""
    try:
        if not device:
            out = _sh("df -Th", timeout=8)
            lsblk = _sh("lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT", timeout=8)
            return {"status": "ok", "df": out, "lsblk": lsblk}
        tune = _sh(f"tune2fs -l {device} 2>/dev/null | grep -E "
                   f"'state|errors|Last checked|Mount count'", timeout=10)
        xfs  = _sh(f"xfs_info {device} 2>/dev/null | head -10", timeout=10)
        mdstat = _sh("cat /proc/mdstat 2>/dev/null", timeout=5)
        return {"status": "ok", "device": device,
                "ext_info": tune, "xfs_info": xfs, "mdstat": mdstat}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 4 — Hardware & performance
# ---------------------------------------------------------------------------

def get_hardware_info() -> dict:
    """Return CPU model, RAM DIMMs, USB and PCI devices."""
    try:
        cpu   = _sh("lscpu | grep -E 'Model name|Socket|Thread|Core|MHz'", timeout=8)
        mem   = _sh(
            "dmidecode -t memory 2>/dev/null | grep -E 'Size|Speed|Type|Locator' "
            "| grep -v 'No Module' | head -30",
            timeout=10)
        usb   = _sh("lsusb 2>/dev/null", timeout=8)
        pci   = _sh("lspci 2>/dev/null | head -20", timeout=8)
        return {"status": "ok", "cpu": cpu, "memory_dimms": mem or "(dmidecode not available)",
                "usb_devices": usb, "pci_devices": pci}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_disk_smart_status(device: str = "") -> dict:
    """Read SMART health and reallocated sector count for storage drives."""
    try:
        if not device:
            drives = _sh(
                "lsblk -o NAME,SIZE,TYPE,TRAN,MODEL -d | grep -v loop", timeout=8)
            return {"status": "ok",
                    "note": "Provide a device path (e.g. /dev/sda) for SMART details.",
                    "drives": drives}
        health = _sh(f"smartctl -H {device} 2>/dev/null", timeout=15)
        attrs  = _sh(
            f"smartctl -A {device} 2>/dev/null | grep -E "
            f"'Reallocated|Pending|Uncorrectable|Power_On|Temp'",
            timeout=15)
        return {"status": "ok", "device": device,
                "health": health, "key_attributes": attrs}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_temperatures() -> dict:
    """Report CPU, GPU and drive temperatures from hardware sensors."""
    try:
        sensors = _sh("sensors 2>/dev/null", timeout=8)
        thermal = _sh(
            "for f in /sys/class/thermal/thermal_zone*/temp; do "
            "echo \"$f: $(cat $f)\"; done 2>/dev/null",
            timeout=5)
        hddtemp = _sh(
            "hddtemp /dev/sd? 2>/dev/null || echo '(hddtemp not available)'",
            timeout=10)
        return {"status": "ok",
                "sensors": sensors or "(lm-sensors not installed)",
                "thermal_zones": thermal,
                "hdd_temp": hddtemp}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_io_stats(duration_seconds: int = 3) -> dict:
    """Measure per-disk I/O utilization and CPU I/O wait over a sample window."""
    try:
        iostat = _sh(
            f"iostat -x 1 {duration_seconds} 2>/dev/null | tail -20",
            timeout=duration_seconds + 10)
        vmstat = _sh(
            f"vmstat 1 {duration_seconds} 2>/dev/null",
            timeout=duration_seconds + 10)
        return {"status": "ok", "iostat": iostat or "(sysstat not installed)",
                "vmstat": vmstat}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_memory_pressure() -> dict:
    """Detailed RAM/swap breakdown, top memory consumers, OOM events."""
    try:
        meminfo = _sh("cat /proc/meminfo | head -20", timeout=5)
        top_mem = _sh("ps aux --sort=-%mem | head -12", timeout=8)
        oom     = _sh(
            "journalctl -k --no-pager -n 20 2>/dev/null | grep -i 'oom\\|killed' "
            "|| echo '(no OOM events found)'",
            timeout=10)
        swap    = _sh("swapon --show 2>/dev/null || echo '(no swap)'", timeout=5)
        return {"status": "ok", "meminfo": meminfo,
                "top_memory_processes": top_mem, "oom_events": oom, "swap": swap}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 5 — Package management diagnostics
# ---------------------------------------------------------------------------

def get_package_info(package_name: str) -> dict:
    """Show installed version, available version and upgrade status."""
    try:
        installed = _sh(f"dpkg -s {package_name} 2>/dev/null | grep -E 'Package|Version|Status'",
                        timeout=8)
        policy    = _sh(f"apt-cache policy {package_name} 2>/dev/null", timeout=10)
        upgradable = _sh(
            f"apt list --upgradable 2>/dev/null | grep {package_name} || echo '(up to date)'",
            timeout=15)
        return {"status": "ok", "package": package_name,
                "dpkg": installed or "(not installed)",
                "apt_policy": policy,
                "upgradable": upgradable}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 6 — Service & cron diagnostics
# ---------------------------------------------------------------------------

def get_service_logs(service_name: str, count: int = 50) -> dict:
    """Return the last N journal log lines for a systemd service."""
    try:
        out = _sh(
            f"journalctl -u {service_name} -n {count} --no-pager -o short 2>/dev/null",
            timeout=15)
        return {"status": "ok", "service": service_name,
                "lines": count, "output": _trunc(out) or "(no logs found)"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def list_cron_jobs(username: str = "") -> dict:
    """List crontab entries and systemd timers for a user or system-wide."""
    try:
        crontab = _sh(
            f"crontab -l -u {username} 2>/dev/null" if username else
            "crontab -l 2>/dev/null",
            timeout=8)
        cron_d  = _sh("ls /etc/cron.d/ /etc/cron.daily/ /etc/cron.weekly/ "
                      "/etc/cron.monthly/ 2>/dev/null", timeout=5)
        timers  = _sh(
            "systemctl list-timers --all --no-pager 2>/dev/null | head -30",
            timeout=10)
        return {"status": "ok", "crontab": crontab or "(empty)",
                "system_cron_dirs": cron_d, "systemd_timers": timers}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

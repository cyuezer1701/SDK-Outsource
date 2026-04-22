"""Remediation tools — each requires Human-in-the-Loop confirmation before execution.

The `explanation` parameter on every function is the plain-language description
shown to the user in the approval prompt. Claude must always populate it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess


def flush_dns_cache(explanation: str) -> dict:
    """Clear the DNS resolver cache.

    Args:
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    try:
        if system == "Windows":
            _run("ipconfig /flushdns")
            return {"status": "ok", "message": "DNS cache flushed (Windows)."}
        elif system == "Darwin":
            _run("sudo dscacheutil -flushcache")
            _run("sudo killall -HUP mDNSResponder")
            return {"status": "ok", "message": "DNS cache flushed (macOS)."}
        else:
            _run("sudo systemd-resolve --flush-caches")
            return {"status": "ok", "message": "DNS cache flushed (Linux/systemd)."}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def restart_service(service_name: str, explanation: str) -> dict:
    """Stop and restart a named Windows/macOS/Linux service.

    Args:
        service_name: The exact service name to restart.
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    try:
        if system == "Windows":
            _run(f'powershell -Command "Restart-Service -Name \'{service_name}\' -Force"')
            return {"status": "ok", "message": f"Service '{service_name}' restarted (Windows)."}
        elif system == "Darwin":
            _run(f"sudo launchctl kickstart -k system/{service_name}")
            return {"status": "ok", "message": f"Service '{service_name}' restarted (macOS)."}
        else:
            _run(f"sudo systemctl restart {service_name}")
            return {"status": "ok", "message": f"Service '{service_name}' restarted (Linux)."}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def kill_process(process_name: str, explanation: str) -> dict:
    """Force-terminate all running instances of a named process.

    Args:
        process_name: Process name (without path), e.g. 'OUTLOOK.EXE' or 'Outlook'.
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    try:
        if system == "Windows":
            _run(f'taskkill /IM "{process_name}" /F')
            return {"status": "ok", "message": f"Process '{process_name}' terminated (Windows)."}
        else:
            _run(f"pkill -x '{process_name}' || pkill -f '{process_name}'")
            return {"status": "ok", "message": f"Process '{process_name}' terminated."}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def clear_app_cache(app_name: str, explanation: str) -> dict:
    """Delete the cache folder for a specific application.

    Args:
        app_name: Application name, e.g. 'Outlook', 'Teams', 'Chrome'.
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    deleted_paths: list[str] = []
    errors: list[str] = []

    cache_candidates = _get_cache_paths(app_name, system)

    for path in cache_candidates:
        if os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                deleted_paths.append(path)
            except Exception as exc:
                errors.append(f"{path}: {exc}")

    if not cache_candidates:
        return {
            "status": "error",
            "error": f"No known cache path for '{app_name}' on {system}.",
        }

    return {
        "status": "ok" if not errors else "partial",
        "deleted": deleted_paths,
        "errors": errors,
        "message": (
            f"Cleared {len(deleted_paths)} cache location(s) for '{app_name}'."
            if deleted_paths
            else f"No cache directories found for '{app_name}'."
        ),
    }


def restart_network_adapter(adapter_name: str, explanation: str) -> dict:
    """Disable then re-enable a named network adapter to reset the connection.

    Args:
        adapter_name: Adapter name as shown in network settings (Windows) or
                      interface name (macOS/Linux, e.g. 'en0', 'eth0').
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    try:
        if system == "Windows":
            _run(
                f'powershell -Command "Disable-NetAdapter -Name \'{adapter_name}\' -Confirm:$false; '
                f'Start-Sleep -Seconds 2; '
                f'Enable-NetAdapter -Name \'{adapter_name}\' -Confirm:$false"'
            )
            return {
                "status": "ok",
                "message": f"Adapter '{adapter_name}' restarted (Windows).",
            }
        elif system == "Darwin":
            _run(f"sudo ifconfig {adapter_name} down")
            _run(f"sudo ifconfig {adapter_name} up")
            return {
                "status": "ok",
                "message": f"Interface '{adapter_name}' restarted (macOS).",
            }
        else:
            _run(f"sudo ip link set {adapter_name} down && sudo ip link set {adapter_name} up")
            return {
                "status": "ok",
                "message": f"Interface '{adapter_name}' restarted (Linux).",
            }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def install_package(package_name: str, explanation: str) -> dict:
    """Install a software package via the system package manager.

    Args:
        package_name: Package name to install, e.g. 'curl', 'htop', 'nginx'.
        explanation: Plain-language explanation shown to the user before approval.
    """
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(["winget", "--version"], capture_output=True)
            if result.returncode == 0:
                _run(f'winget install --id "{package_name}" --silent --accept-package-agreements --accept-source-agreements', timeout=120)
                return {"status": "ok", "message": f"Package '{package_name}' installed via winget."}
            else:
                _run(f'choco install "{package_name}" -y', timeout=120)
                return {"status": "ok", "message": f"Package '{package_name}' installed via chocolatey."}
        elif system == "Darwin":
            _run(f"brew install {package_name}", timeout=120)
            return {"status": "ok", "message": f"Package '{package_name}' installed via Homebrew."}
        else:
            # Use sudo if not already root
            prefix = "" if os.geteuid() == 0 else "sudo "
            _run(f"{prefix}apt-get update -qq", timeout=60)
            output = _run(f"{prefix}DEBIAN_FRONTEND=noninteractive apt-get install -y {package_name}", timeout=900)
            return {"status": "ok", "message": f"Package '{package_name}' installed via apt.", "output": output}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, timeout: int = 30) -> str:
    """Run a shell command and return combined stdout+stderr."""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0 and result.stderr:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def _get_cache_paths(app_name: str, system: str) -> list[str]:
    """Return known cache directory candidates for an application."""
    name_lower = app_name.lower()

    # Windows paths
    if system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "")
        app_data = os.environ.get("APPDATA", "")
        candidates: dict[str, list[str]] = {
            "outlook": [
                os.path.join(local_app, "Microsoft", "Outlook"),
            ],
            "teams": [
                os.path.join(app_data, "Microsoft", "Teams", "Cache"),
                os.path.join(app_data, "Microsoft", "Teams", "blob_storage"),
            ],
            "chrome": [
                os.path.join(local_app, "Google", "Chrome", "User Data", "Default", "Cache"),
            ],
            "edge": [
                os.path.join(local_app, "Microsoft", "Edge", "User Data", "Default", "Cache"),
            ],
            "firefox": [
                os.path.join(local_app, "Mozilla", "Firefox", "Profiles"),
            ],
        }
        return candidates.get(name_lower, [])

    # macOS paths
    if system == "Darwin":
        home = os.path.expanduser("~")
        candidates = {
            "outlook": [os.path.join(home, "Library", "Group Containers", "UBF8T346G9.Office")],
            "teams": [os.path.join(home, "Library", "Application Support", "Microsoft", "Teams")],
            "chrome": [os.path.join(home, "Library", "Caches", "Google", "Chrome")],
            "safari": [os.path.join(home, "Library", "Caches", "com.apple.Safari")],
            "firefox": [os.path.join(home, "Library", "Caches", "Firefox")],
        }
        return candidates.get(name_lower, [])

    # Linux paths
    home = os.path.expanduser("~")
    candidates = {
        "chrome": [os.path.join(home, ".cache", "google-chrome")],
        "firefox": [os.path.join(home, ".cache", "mozilla", "firefox")],
        "teams": [os.path.join(home, ".config", "Microsoft", "Microsoft Teams")],
    }
    return candidates.get(name_lower, [])

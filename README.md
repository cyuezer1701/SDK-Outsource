# AI-Powered IT Service Desk Agent

A local, AI-driven IT support agent that runs on employee laptops. It diagnoses system problems step-by-step using read-only tools, then proposes targeted fixes that **require explicit human approval** before execution.

## Architecture

```
Employee Laptop
│
├─ main.py               Rich CLI — natural language input/output
│       │
│       ▼
├─ agent/core.py         Agentic loop — Claude API tool_use
│       │
│       ├──► tools/diagnostic.py    Read-only (auto-execute)
│       │        psutil · subprocess · platform
│       │
│       └──► tools/remediation.py   Write ops (Human-in-the-Loop)
│                subprocess (PowerShell / bash / sh)
│
├─ guardrails/classifier.py
│       SAFE | REQUIRES_APPROVAL | BLOCKED
│
└─ .env   (ANTHROPIC_API_KEY)
          │
          ▼
    Anthropic Cloud API  (claude-sonnet-4-6)
```

## Prerequisites

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/)
- Internet access (outgoing HTTPS to `api.anthropic.com` only)

## Installation

```bash
git clone <repo-url>
cd SDK-Outsource

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
python main.py
```

Describe your IT problem in plain English:

```
You: My internet is not working
You: Outlook is completely frozen
You: My computer is running very slowly
You: I can't print anything
```

Type `exit` or `quit` to leave.

## Security Model

| Action type | Behaviour |
|---|---|
| **Diagnostic** (read-only) | Executes automatically — no prompt |
| **Remediation** (write ops) | Yellow panel shown with plain-language explanation; Y/N required |
| **Blocked commands** | Refused outright; agent informs user |

### Blocked patterns (examples)

- `rm -rf /`, `format C:`, `diskpart`
- `dd if=... of=/dev/...`, `mkfs`, `wipefs`
- `REG DELETE HKLM\SYSTEM`, `shutdown /r`
- Fork bombs and similar destructive patterns

### Remediation tools (all require Y approval)

| Tool | What it does |
|---|---|
| `flush_dns_cache` | Clear the OS DNS resolver cache |
| `restart_service` | Stop and restart a named system service |
| `kill_process` | Force-quit all instances of a named process |
| `clear_app_cache` | Delete known cache directories for an application |
| `restart_network_adapter` | Disable then re-enable a named network interface |

## Supported Platforms

| Feature | Windows 10/11 | macOS 12+ | Linux (systemd) |
|---|---|---|---|
| System info | ✓ | ✓ | ✓ |
| Process list | ✓ | ✓ | ✓ |
| Network info | ✓ | ✓ | ✓ |
| Disk info | ✓ | ✓ | ✓ |
| Service status | PowerShell | launchctl | systemctl |
| Event log | Get-EventLog | log show | journalctl |
| Flush DNS | ipconfig /flushdns | dscacheutil | systemd-resolve |
| Restart service | Restart-Service | launchctl | systemctl restart |
| Kill process | taskkill /F | pkill | pkill |
| Clear cache | LOCALAPPDATA | ~/Library | ~/.cache |
| Restart adapter | Disable/Enable-NetAdapter | ifconfig | ip link |

> **Permissions note:** Remediation actions on Windows require an elevated (Admin) PowerShell session. On macOS/Linux they use `sudo` and will prompt for your password if needed.

## Test Scenarios

| Input | Expected agent path |
|---|---|
| "My browser says DNS_PROBE_FINISHED_NXDOMAIN" | `get_network_info` → `flush_dns_cache` (approval) |
| "Outlook is frozen" | `get_running_processes("outlook")` → `kill_process` (approval) |
| "Laptop is very slow" | `get_system_info` → `get_running_processes` → `kill_process` (approval) |
| "Can't print" | `get_services_status(["Spooler"])` → `restart_service` (approval) |
| "No WiFi" | `get_network_info` → `restart_network_adapter` (approval) |
| "Delete all files" | Blocked; agent explains why |

### Verify prompt caching

Check the Anthropic API response for `cache_read_input_tokens > 0` on the second and subsequent tool calls within a session. The system prompt is cached with `cache_control: ephemeral` and reused across all iterations of the agentic loop.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Your Anthropic API key |
| `AGENT_MODEL` | `claude-sonnet-4-6` | Claude model to use |

## Project Structure

```
SDK-Outsource/
├── agent/
│   ├── __init__.py
│   └── core.py            # Agentic loop + Claude API integration
├── tools/
│   ├── __init__.py
│   ├── diagnostic.py      # Read-only tools (auto-execute)
│   └── remediation.py     # Write tools (require user approval)
├── guardrails/
│   ├── __init__.py
│   └── classifier.py      # SAFE / REQUIRES_APPROVAL / BLOCKED
├── main.py                # Rich CLI entry point
├── requirements.txt
├── .env.example
└── README.md
```

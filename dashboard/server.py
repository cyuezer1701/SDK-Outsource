"""L2 Support Dashboard — Flask web server.

Run standalone:  python3 -m dashboard.server
Or import:       from dashboard.server import start_background
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# Ensure project root is on the path when run as __main__
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flask import Flask, jsonify, redirect, render_template_string, request, url_for
from tickets.store import list_tickets, update_status

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── HTML template ─────────────────────────────────────────────────────────────

_BASE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IT Service Desk — L2 Dashboard</title>
<style>
  :root{--bg:#080C18;--panel:#0D1526;--surface:#111D33;--border:#1A2B45;
        --cyan:#00D4FF;--green:#00E676;--yellow:#FFB800;--red:#FF4560;
        --text:#E2ECF8;--muted:#3D5270;--purple:#8B5CF6}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
  a{color:var(--cyan);text-decoration:none}
  a:hover{text-decoration:underline}

  /* Header */
  .header{background:var(--panel);border-bottom:1px solid var(--border);
          padding:14px 28px;display:flex;align-items:center;gap:16px}
  .header .dot{width:10px;height:10px;border-radius:50%;background:var(--green);flex-shrink:0}
  .header h1{font-family:monospace;font-size:16px;color:var(--cyan);letter-spacing:.08em}
  .header .sub{font-size:12px;color:var(--muted);margin-left:4px}
  .header .badge{margin-left:auto;font-family:monospace;font-size:11px;
                 background:var(--surface);border:1px solid var(--border);
                 padding:4px 12px;border-radius:20px;color:var(--muted)}

  /* Layout */
  .container{max-width:1100px;margin:0 auto;padding:28px 20px}

  /* Stats row */
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px}
  .stat-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;
             padding:16px 20px}
  .stat-card .num{font-size:28px;font-weight:700;font-family:monospace}
  .stat-card .lbl{font-size:11px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.06em}
  .open{color:var(--yellow)} .inprog{color:var(--cyan)} .closed{color:var(--green)}

  /* Filter bar */
  .filterbar{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
  .filterbar a{padding:6px 16px;border-radius:20px;font-size:12px;font-weight:600;
               border:1px solid var(--border);background:var(--surface);color:var(--muted);
               transition:all .15s}
  .filterbar a.active,.filterbar a:hover{border-color:var(--cyan);color:var(--cyan)}

  /* Ticket table */
  .ticket-table{width:100%;border-collapse:collapse}
  .ticket-table th{text-align:left;font-size:10px;color:var(--muted);text-transform:uppercase;
                   letter-spacing:.08em;padding:8px 14px;border-bottom:1px solid var(--border)}
  .ticket-table td{padding:12px 14px;border-bottom:1px solid var(--border);vertical-align:top;font-size:13px}
  .ticket-table tr:hover td{background:var(--surface)}

  /* Priority badges */
  .prio{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:700;font-family:monospace}
  .prio-high{background:#3D0A10;color:var(--red);border:1px solid var(--red)}
  .prio-medium{background:#2D1A00;color:var(--yellow);border:1px solid var(--yellow)}
  .prio-low{background:#0A2A10;color:var(--green);border:1px solid var(--green)}

  /* Status badges */
  .status{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;font-family:monospace}
  .status-open{background:#1A1000;color:var(--yellow);border:1px solid var(--yellow)}
  .status-in_progress{background:#001A1A;color:var(--cyan);border:1px solid var(--cyan)}
  .status-closed{background:#0A1A0A;color:var(--green);border:1px solid var(--green)}

  /* Detail page */
  .detail{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:28px}
  .detail h2{font-family:monospace;font-size:18px;color:var(--cyan);margin-bottom:4px}
  .detail .meta{font-size:12px;color:var(--muted);margin-bottom:24px}
  .section{margin-bottom:20px}
  .section h3{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;
              margin-bottom:8px}
  .section pre{background:var(--surface);border:1px solid var(--border);border-radius:8px;
               padding:14px;font-size:12px;white-space:pre-wrap;word-break:break-word;
               color:var(--text);line-height:1.6}
  .sysinfo{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .sysinfo-item{background:var(--surface);border:1px solid var(--border);border-radius:8px;
                padding:12px 16px}
  .sysinfo-item .k{font-size:10px;color:var(--muted);text-transform:uppercase;margin-bottom:4px}
  .sysinfo-item .v{font-family:monospace;font-size:14px;color:var(--text)}

  /* Action buttons */
  .actions{display:flex;gap:8px;margin-top:24px;flex-wrap:wrap}
  .btn{padding:8px 20px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;
       border:1px solid;transition:opacity .15s}
  .btn:hover{opacity:.8}
  .btn-inprog{background:#001A1A;color:var(--cyan);border-color:var(--cyan)}
  .btn-close{background:#0A2A10;color:var(--green);border-color:var(--green)}
  .btn-reopen{background:#1A1000;color:var(--yellow);border-color:var(--yellow)}
  .empty{text-align:center;padding:60px 20px;color:var(--muted);font-size:14px}
</style>
</head>
<body>
<div class="header">
  <div class="dot"></div>
  <h1>IT SERVICE DESK</h1><span class="sub">L2 Support Dashboard</span>
  <span class="badge">{% block badge %}{% endblock %}</span>
</div>
{% block body %}{% endblock %}
</body>
</html>"""

_INDEX = _BASE.replace("{% block badge %}{% endblock %}", "{{ open_count }} offen").replace(
    "{% block body %}{% endblock %}", """
<div class="container">
  <div class="stats">
    <div class="stat-card"><div class="num open">{{ counts.open }}</div><div class="lbl">Offen</div></div>
    <div class="stat-card"><div class="num inprog">{{ counts.in_progress }}</div><div class="lbl">In Bearbeitung</div></div>
    <div class="stat-card"><div class="num closed">{{ counts.closed }}</div><div class="lbl">Gelöst</div></div>
  </div>

  <div class="filterbar">
    <a href="/?filter=all" class="{{ 'active' if filter=='all' }}">Alle</a>
    <a href="/?filter=open" class="{{ 'active' if filter=='open' }}">Offen</a>
    <a href="/?filter=in_progress" class="{{ 'active' if filter=='in_progress' }}">In Bearbeitung</a>
    <a href="/?filter=closed" class="{{ 'active' if filter=='closed' }}">Gelöst</a>
  </div>

  {% if tickets %}
  <table class="ticket-table">
    <thead><tr>
      <th>ID</th><th>Problem</th><th>Priorität</th><th>Status</th><th>Erstellt</th>
    </tr></thead>
    <tbody>
    {% for t in tickets %}
    <tr>
      <td><a href="/ticket/{{ t.id }}" style="font-family:monospace;font-weight:700">{{ t.id }}</a></td>
      <td><a href="/ticket/{{ t.id }}">{{ t.problem }}</a></td>
      <td><span class="prio prio-{{ t.priority }}">{{ t.priority.upper() }}</span></td>
      <td><span class="status status-{{ t.status }}">{{ t.status.replace('_',' ').upper() }}</span></td>
      <td style="color:var(--muted);font-family:monospace;font-size:11px">{{ t.created_at }}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">✓ Keine Tickets in dieser Ansicht</div>
  {% endif %}
</div>
""")

_DETAIL = _BASE.replace("{% block badge %}{% endblock %}", "{{ ticket.id }}").replace(
    "{% block body %}{% endblock %}", """
<div class="container">
  <p style="margin-bottom:16px;font-size:12px">
    <a href="/">← Zurück zur Übersicht</a>
  </p>
  <div class="detail">
    <div style="display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap">
      <div>
        <h2>{{ ticket.id }} — {{ ticket.problem }}</h2>
        <div class="meta">
          Erstellt: {{ ticket.created_at }} &nbsp;·&nbsp;
          <span class="prio prio-{{ ticket.priority }}">{{ ticket.priority.upper() }}</span> &nbsp;·&nbsp;
          <span class="status status-{{ ticket.status }}">{{ ticket.status.replace('_',' ').upper() }}</span>
        </div>
      </div>
    </div>

    <div class="section">
      <h3>Zusammenfassung</h3>
      <pre>{{ ticket.summary }}</pre>
    </div>

    <div class="section">
      <h3>Bereits probierte Schritte</h3>
      <pre>{{ ticket.tried_steps }}</pre>
    </div>

    {% if sysinfo %}
    <div class="section">
      <h3>System-Informationen</h3>
      <div class="sysinfo">
        {% for k, v in sysinfo.items() %}{% if k != 'status' %}
        <div class="sysinfo-item"><div class="k">{{ k }}</div><div class="v">{{ v }}</div></div>
        {% endif %}{% endfor %}
      </div>
    </div>
    {% endif %}

    <div class="actions">
      {% if ticket.status != 'in_progress' and ticket.status != 'closed' %}
      <form method="post" action="/ticket/{{ ticket.id }}/status">
        <input type="hidden" name="status" value="in_progress">
        <button type="submit" class="btn btn-inprog">→ In Bearbeitung</button>
      </form>
      {% endif %}
      {% if ticket.status != 'closed' %}
      <form method="post" action="/ticket/{{ ticket.id }}/status">
        <input type="hidden" name="status" value="closed">
        <button type="submit" class="btn btn-close">✓ Als gelöst markieren</button>
      </form>
      {% endif %}
      {% if ticket.status == 'closed' %}
      <form method="post" action="/ticket/{{ ticket.id }}/status">
        <input type="hidden" name="status" value="open">
        <button type="submit" class="btn btn-reopen">↩ Wieder öffnen</button>
      </form>
      {% endif %}
    </div>
  </div>
</div>
""")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    f = request.args.get("filter", "open")
    status_filter = None if f == "all" else f
    tickets = list_tickets(status=status_filter)
    all_tickets = list_tickets()
    counts = {
        "open":        sum(1 for t in all_tickets if t.status == "open"),
        "in_progress": sum(1 for t in all_tickets if t.status == "in_progress"),
        "closed":      sum(1 for t in all_tickets if t.status == "closed"),
    }
    return render_template_string(
        _INDEX, tickets=tickets, filter=f,
        counts=counts, open_count=counts["open"],
    )


@app.route("/ticket/<ticket_id>")
def ticket_detail(ticket_id: str):
    all_tickets = list_tickets()
    ticket = next((t for t in all_tickets if t.id == ticket_id), None)
    if ticket is None:
        return "Ticket nicht gefunden", 404
    try:
        sysinfo = json.loads(ticket.system_info)
    except Exception:
        sysinfo = {}
    return render_template_string(_DETAIL, ticket=ticket, sysinfo=sysinfo)


@app.route("/ticket/<ticket_id>/status", methods=["POST"])
def set_status(ticket_id: str):
    new_status = request.form.get("status", "open")
    update_status(ticket_id, new_status)
    return redirect(f"/ticket/{ticket_id}")


@app.route("/api/tickets")
def api_tickets():
    f = request.args.get("status")
    tickets = list_tickets(status=f)
    return jsonify([{
        "id": t.id, "problem": t.problem, "priority": t.priority,
        "status": t.status, "created_at": t.created_at,
    } for t in tickets])


# ── Background launcher (for GUI) ─────────────────────────────────────────────

_server_thread: threading.Thread | None = None
_PORT = int(os.environ.get("DASHBOARD_PORT", "8099"))


def start_background(port: int = _PORT) -> int:
    """Start the Flask server in a daemon thread. Returns the port number."""
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return port

    def _run():
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    return port


if __name__ == "__main__":
    print(f"L2 Dashboard läuft auf http://127.0.0.1:{_PORT}")
    app.run(host="0.0.0.0", port=_PORT, debug=False)

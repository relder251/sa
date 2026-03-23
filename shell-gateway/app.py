"""
shell-gateway — browser-based terminal proxy for Docker containers + VPS host.

Routes:
  GET  /terminal/<target>        → xterm.js HTML page
  WS   /terminal/socket.io/      → pty bridge (start / in / resize / disconnect)

Allowed targets are defined in TARGETS below.  Each maps to the command that
is exec'd for every new WebSocket session.  'vps' uses nsenter to enter the
host's namespaces (requires the container to run with pid:host + privileged).
"""
import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import threading

import flask
from flask import Flask, abort
from flask_socketio import SocketIO, disconnect

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(24))

socketio = SocketIO(
    app,
    async_mode="threading",
    cors_allowed_origins="*",
    path="/terminal/socket.io",
)

# ── Allowed targets ──────────────────────────────────────────────────────────
# Keep this list explicit — never interpolate user input into a shell command.
TARGETS: dict[str, list[str]] = {
    "n8n":          ["docker", "exec", "-it", "n8n",          "sh"],   # n8n image has sh only
    "webui":        ["docker", "exec", "-it", "webui",        "bash"],
    "litellm":      ["docker", "exec", "-it", "litellm",      "bash"],
    "jupyter":      ["docker", "exec", "-it", "jupyter",      "bash"],
    "glitchtip_web":["docker", "exec", "-it", "glitchtip_web","bash"],
    "vaultwarden":  ["docker", "exec", "-it", "vaultwarden",  "sh"],   # alpine-based, sh only
    "keycloak":     ["docker", "exec", "-it", "keycloak",     "bash"],
    # nsenter enters host namespaces; requires pid:host + privileged in compose
    "vps":          ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "bash"],
    "hostinger":    ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "bash"],
}

# ── Terminal HTML (xterm.js + socket.io) ─────────────────────────────────────
# Uses str.replace so we don't have to escape every JS { } with {{ }}
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>⌨ __TARGET__</title>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: #0d1117; overflow: hidden; }
    #bar {
      height: 32px; display: flex; align-items: center; gap: 8px;
      padding: 0 14px; background: #161b22; color: #8b949e;
      font: 12px/1 'Cascadia Code', 'Fira Code', monospace;
      border-bottom: 1px solid #30363d; user-select: none; flex-shrink: 0;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #3fb950; flex-shrink: 0; }
    .dot.dead { background: #f85149; }
    #wrap { height: calc(100% - 32px); padding: 4px; }
  </style>
</head>
<body>
<div id="bar">
  <span class="dot" id="dot"></span>
  ⌨ __TARGET__
</div>
<div id="wrap">
  <div id="term" style="height:100%"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
<script src="https://cdn.jsdelivr.net/npm/socket.io-client@4.7.2/dist/socket.io.min.js"></script>
<script>
const TARGET = '__TARGET__';

const term = new Terminal({
  cursorBlink: true,
  fontSize: 14,
  fontFamily: '"Cascadia Code","Fira Code","JetBrains Mono",monospace',
  theme: {
    background: '#0d1117', foreground: '#e6edf3',
    cursor: '#58a6ff', selectionBackground: 'rgba(88,166,255,0.3)'
  }
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));
fit.fit();
term.focus();

const socket = io({ path: '/terminal/socket.io' });

socket.on('connect', () => {
  socket.emit('start', { target: TARGET, rows: term.rows, cols: term.cols });
});

socket.on('out', data => term.write(data));

socket.on('exit', () => {
  term.write('\r\n\x1b[90m[session closed — you may close this tab]\x1b[0m\r\n');
  document.getElementById('dot').classList.add('dead');
});

socket.on('err', msg => {
  term.write('\r\n\x1b[31m[error: ' + msg + ']\x1b[0m\r\n');
  document.getElementById('dot').classList.add('dead');
});

term.onData(d => socket.emit('in', d));
term.onResize(({ rows, cols }) => socket.emit('resize', { rows, cols }));

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => fit.fit(), 80);
});
</script>
</body>
</html>
"""

# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.route("/terminal/<target>")
def terminal_page(target: str):
    if target not in TARGETS:
        abort(404)
    return _HTML_TEMPLATE.replace("__TARGET__", target)


@app.route("/health")
def health():
    return {"status": "ok"}


# ── WebSocket / pty bridge ────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}  # sid → {proc, fd}


@socketio.on("start")
def on_start(data: dict):
    target = data.get("target", "")
    if target not in TARGETS:
        socketio.emit("err", f"unknown target: {target!r}", room=flask.request.sid)
        disconnect()
        return

    rows = max(1, int(data.get("rows", 24)))
    cols = max(1, int(data.get("cols", 80)))
    cmd  = TARGETS[target]
    sid  = flask.request.sid

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=slave, stdout=slave, stderr=slave,
            close_fds=True,
            preexec_fn=os.setsid,
        )
    except (FileNotFoundError, PermissionError) as exc:
        os.close(master)
        os.close(slave)
        socketio.emit("err", str(exc), room=sid)
        return

    os.close(slave)
    _sessions[sid] = {"proc": proc, "fd": master}

    def _reader():
        while True:
            try:
                r, _, _ = select.select([master], [], [], 0.05)
                if r:
                    chunk = os.read(master, 4096)
                    if chunk:
                        socketio.emit(
                            "out",
                            chunk.decode("utf-8", errors="replace"),
                            room=sid,
                        )
                if proc.poll() is not None:
                    socketio.emit("exit", {}, room=sid)
                    break
            except OSError:
                socketio.emit("exit", {}, room=sid)
                break

    threading.Thread(target=_reader, daemon=True).start()


@socketio.on("in")
def on_input(data):
    sess = _sessions.get(flask.request.sid)
    if sess:
        try:
            os.write(sess["fd"], data.encode() if isinstance(data, str) else data)
        except OSError:
            pass


@socketio.on("resize")
def on_resize(data: dict):
    sess = _sessions.get(flask.request.sid)
    if sess:
        rows = max(1, int(data.get("rows", 24)))
        cols = max(1, int(data.get("cols", 80)))
        try:
            fcntl.ioctl(
                sess["fd"], termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0),
            )
        except OSError:
            pass


@socketio.on("disconnect")
def on_disconnect():
    sess = _sessions.pop(flask.request.sid, None)
    if sess:
        try:
            sess["proc"].terminate()
        except Exception:
            pass
        try:
            os.close(sess["fd"])
        except OSError:
            pass


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=7681, allow_unsafe_werkzeug=True)

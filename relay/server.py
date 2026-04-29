#!/usr/bin/env python3
"""
Appdesk Relay Server
====================
Deploy this single file on any public VPS (e.g. Oracle Cloud Free Tier).

Usage:
    python3 server.py

One TCP port (5902) handles everything.  After a short JSON handshake the
relay pipes raw bytes bidirectionally between host and client — it never
inspects the stream content.

Session life-cycle:
  1. Host connects, sends {"role":"host","channel":"screen"}
     → relay assigns a 6-char ID, responds {"ok":true,"id":"AB3X7K"}
  2. Host connects again, sends {"role":"host","channel":"input","id":"AB3X7K"}
     → relay responds {"ok":true}
  3. Client connects twice (screen + input) sending the same ID
     → relay pairs each channel and starts piping
  4. When either side disconnects, both sides of that channel are closed.
"""

from __future__ import annotations

import json
import random
import socket
import string
import threading
import time

PORT         = 5902
PAIR_TIMEOUT = 300   # seconds a channel waits for its peer before giving up


# ── session data structures ────────────────────────────────────────────────────

class _Slot:
    """Holds one channel (screen or input) and pairs host ↔ client."""

    def __init__(self) -> None:
        self.host:   socket.socket | None = None
        self.client: socket.socket | None = None
        self._event = threading.Event()
        self._lock  = threading.Lock()
        self._piped = False

    def add(self, role: str, sock: socket.socket) -> None:
        if role == "host":
            self.host = sock
        else:
            self.client = sock
        if self.host and self.client:
            self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def claim_pipe(self) -> bool:
        """Returns True exactly once — the caller must start the pipe threads."""
        with self._lock:
            if self._piped:
                return False
            self._piped = True
            return True


class _Session:
    def __init__(self, sid: str) -> None:
        self.id      = sid
        self.screen  = _Slot()
        self.input   = _Slot()
        self.created = time.time()


_sessions: dict[str, _Session] = {}
_registry_lock = threading.Lock()


def _gen_id() -> str:
    chars = string.ascii_uppercase + string.digits
    with _registry_lock:
        while True:
            sid = "".join(random.choices(chars, k=6))
            if sid not in _sessions:
                _sessions[sid] = None  # reserve slot to prevent duplicates
                return sid


# ── protocol helpers ───────────────────────────────────────────────────────────

def _recv_line(sock: socket.socket) -> str:
    """Read one newline-terminated UTF-8 line (handshake only)."""
    sock.settimeout(15.0)
    buf = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("connection closed during handshake")
        if ch == b"\n":
            break
        buf += ch
        if len(buf) > 4096:
            raise ValueError("handshake line too long")
    sock.settimeout(None)
    return buf.decode("utf-8")


def _send(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """Forward all bytes from src → dst, then close both."""
    try:
        while chunk := src.recv(65536):
            dst.sendall(chunk)
    except OSError:
        pass
    for s in (src, dst):
        try:
            s.close()
        except OSError:
            pass


# ── connection handler (one thread per TCP connection) ─────────────────────────

def _handle(conn: socket.socket, addr: tuple) -> None:
    piped = False   # True once pipe threads own the socket lifecycle
    try:
        msg  = json.loads(_recv_line(conn))
        role: str = msg["role"]      # "host" | "client"
        chan: str  = msg["channel"]  # "screen" | "input"
        sid: str   = msg.get("id", "")

        if role == "host" and chan == "screen":
            # Create a new session and assign a fresh ID
            sid     = _gen_id()
            session = _Session(sid)
            with _registry_lock:
                _sessions[sid] = session
            _send(conn, {"ok": True, "id": sid})
            print(f"[host-screen ] id={sid}  {addr[0]}", flush=True)
            slot = session.screen

        else:
            with _registry_lock:
                session = _sessions.get(sid)
            if not session:
                _send(conn, {"ok": False, "error": "session not found"})
                return
            _send(conn, {"ok": True})
            slot = session.screen if chan == "screen" else session.input
            print(f"[{role}-{chan:<7}] id={sid}  {addr[0]}", flush=True)

        # Register this side of the channel
        slot.add(role, conn)

        # Wait for the peer to connect
        if not slot.wait(PAIR_TIMEOUT):
            print(f"[timeout] id={sid} {chan}", flush=True)
            conn.close()
            return

        # Both sides present — start piping (exactly once per slot)
        piped = True   # pipe threads (or the other handler) now own the sockets
        if slot.claim_pipe():
            h, c = slot.host, slot.client
            threading.Thread(target=_pipe, args=(h, c), daemon=True).start()
            threading.Thread(target=_pipe, args=(c, h), daemon=True).start()

    except Exception as exc:
        print(f"[-] {addr}: {exc}", flush=True)
    finally:
        if not piped:
            try:
                conn.close()
            except OSError:
                pass


# ── periodic cleanup ───────────────────────────────────────────────────────────

def _cleanup() -> None:
    while True:
        time.sleep(60)
        cutoff = time.time() - 600
        with _registry_lock:
            stale = [k for k, v in _sessions.items() if v and v.created < cutoff]
            for k in stale:
                del _sessions[k]
                print(f"[cleanup] removed stale session {k}", flush=True)


# ── entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", PORT))
    srv.listen(128)
    print(f"Appdesk relay listening on :{PORT}", flush=True)
    threading.Thread(target=_cleanup, daemon=True).start()
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()

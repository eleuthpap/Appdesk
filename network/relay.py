"""Relay handshake helpers shared by host and client modules."""
import json
import socket


def recv_line(sock: socket.socket, timeout: float = 15.0) -> str:
    """Read one newline-terminated UTF-8 line from sock (handshake only)."""
    sock.settimeout(timeout)
    buf = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("Connection closed during relay handshake")
        if ch == b"\n":
            break
        buf += ch
        if len(buf) > 4096:
            raise ValueError("Relay handshake line too long")
    sock.settimeout(None)
    return buf.decode("utf-8")


def handshake(sock: socket.socket, msg: dict) -> dict:
    """Send msg as JSON, read and return the relay's JSON response.
    Raises ConnectionError if the relay rejects the request."""
    sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    resp = json.loads(recv_line(sock))
    if not resp.get("ok"):
        raise ConnectionError(
            f"Relay rejected handshake: {resp.get('error', 'unknown')}"
        )
    return resp

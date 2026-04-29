from __future__ import annotations

import io
import queue
import socket
import struct
import threading

from PIL import Image

import config


class ScreenReceiver:
    """
    Connects to a ScreenServer and places decoded PIL Images into frame_queue.

    Call connect() first (blocks briefly), then start() to begin the receive loop.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.frame_queue: queue.Queue = queue.Queue(maxsize=2)

    # ------------------------------------------------------------------ public

    def connect(self, session_id: str = "", relay_addr: str = "") -> None:
        """Open TCP connection. Use relay when session_id+relay_addr are supplied."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if session_id and relay_addr:
            host, port_str = relay_addr.rsplit(":", 1)
            self._sock.connect((host.strip(), int(port_str.strip())))
            from network.relay import handshake

            handshake(
                self._sock,
                {"role": "client", "channel": "screen", "id": session_id.strip()},
            )
        else:
            self._sock.connect((self._host, config.SCREEN_PORT))

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # ----------------------------------------------------------------- private

    def _recv_exactly(self, n: int) -> bytes:
        data = bytearray(n)
        view = memoryview(data)
        received = 0
        while received < n:
            chunk_size = self._sock.recv_into(view[received:], n - received)
            if chunk_size == 0:
                raise ConnectionError("Connection closed by host")
            received += chunk_size
        return bytes(data)

    def _receive_loop(self) -> None:
        try:
            while self._running:
                # JPEG frame
                size = struct.unpack(">L", self._recv_exactly(4))[0]
                jpeg = self._recv_exactly(size)
                # Cursor name
                cursor_len = struct.unpack("B", self._recv_exactly(1))[0]
                cursor_name = (
                    self._recv_exactly(cursor_len).decode("ascii")
                    if cursor_len else "arrow"
                )
                # Wall-clock send timestamp
                sent_time = struct.unpack(">d", self._recv_exactly(8))[0]

                img = Image.open(io.BytesIO(jpeg))
                img.load()
                # Drop stale frame if the consumer is slow (prefer freshness)
                if self.frame_queue.full():
                    try:
                        self.frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.frame_queue.put((img, cursor_name, sent_time))
        except (OSError, ConnectionError):
            pass
        finally:
            self._running = False   # signals SessionWindow to stop polling

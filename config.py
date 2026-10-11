# ── Appdesk configuration ──────────────────────────────────────────────────────

SCREEN_PORT: int = 5900   # Host streams frames to client on this port
INPUT_PORT:  int = 5901   # Client sends input events to host on this port

JPEG_QUALITY: int = 70    # 1-95; lower = faster, higher = sharper
FPS:          int = 60    # Maximum capture / stream FPS; slow links run lower
MAX_IN_FLIGHT_FRAMES: int = 16  # Pipeline ceiling; reduced if frames queue up

STREAM_WIDTH:  int = 1600  # Resolution frames are resized to before sending
STREAM_HEIGHT: int = 900

UI_POLL_MS: int = 5        # How often the client checks for a new frame
MOUSE_MOVE_MAX_HZ: int = 120  # Max forwarded mouse-move events per second

# Default relay endpoint for cross-network sessions (host:port)
RELAY_SERVER = "136.64.154.29:5902" # google
# RELAY_SERVER = "91.99.214.41:5902"

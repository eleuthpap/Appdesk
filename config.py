# ── Appdesk configuration ──────────────────────────────────────────────────────

SCREEN_PORT: int = 5900   # Host streams frames to client on this port
INPUT_PORT:  int = 5901   # Client sends input events to host on this port

JPEG_QUALITY: int = 70    # 1-95; lower = faster, higher = sharper
FPS:          int = 30    # Target capture / stream frame rate

STREAM_WIDTH:  int = 1600  # Resolution frames are resized to before sending
STREAM_HEIGHT: int = 900

UI_POLL_MS: int = 15       # How often the client checks for a new frame
MOUSE_MOVE_MAX_HZ: int = 120  # Max forwarded mouse-move events per second

# Default relay endpoint for cross-network sessions (host:port)
RELAY_SERVER = "35.209.231.40:5902" # google
# RELAY_SERVER = "91.99.214.41:5902"

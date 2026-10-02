# Appdesk README (Very Beginner, Step by Step)

This guide explains exactly how to run Appdesk in direct LAN mode and relay mode.

Relay mode means:
- The host computer shares screen + accepts control.
- The client computer connects using a Session ID.
- Both talk through a relay server on the internet.

Direct LAN mode means:
- The host computer shares screen + accepts control.
- The client connects directly to the Host local IP address.
- No Relay Server or Session ID is required.

## 1) What this project is

Appdesk is a simple remote desktop app in Python.

It has 3 parts:
1. Host side: captures screen and executes mouse/keyboard from remote user.
2. Client side: shows remote screen and sends mouse/keyboard actions.
3. Relay server: pairs host and client across networks.

## 2) Files and what they do (simple map)

- main.py
  - Starts the Tkinter app window.
- config.py
  - Global settings (ports, FPS, stream size, relay address).
- ui/main_window.py
  - Main screen with Host and Join controls.
- ui/host_panel.py
  - Host status window (shows Session ID in relay mode).
- ui/session_window.py
  - Client remote session window (shows stream, sends input events).
- host/screen_server.py
  - Captures screen, compresses JPEG, sends frames.
- host/input_executor.py
  - Receives input events and performs mouse/keyboard actions.
- client/screen_receiver.py
  - Receives and decodes streamed frames.
- client/input_forwarder.py
  - Sends mouse/keyboard events to host.
- network/relay.py
  - Shared relay handshake helpers.
- relay/server.py
  - Standalone relay server process for internet pairing.
- requirements.txt
  - Python dependency list.

## 3) Relay mode method used by this app (how it works, step by step)

1. Host clicks Start Hosting.
2. host/screen_server.py connects to relay and asks for a new session.
3. Relay returns a 6-character Session ID.
4. Host shows this Session ID in UI.
5. Host also opens input channel to relay with same Session ID.
6. Client enters Session ID and clicks Connect.
7. Client opens two relay channels:
   - screen channel (receive frames)
   - input channel (send mouse/keyboard)
8. Relay pairs host and client channels and starts forwarding raw bytes both ways.
9. Client sees live screen.
10. Client mouse/keyboard is sent as JSON events and executed on host.

## 4) Ports and protocol used

- Direct LAN mode ports:
  - SCREEN_PORT = 5900
  - INPUT_PORT = 5901
- Relay server port:
  - 5902

Relay handshake is one JSON line, then relay forwards bytes without parsing stream content.

## 5) Install on Windows (host and client machines)

Run these commands in PowerShell or CMD, inside the project folder.

### 5.1 Create virtual environment

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

CMD:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### 5.2 Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 6) Deploy your own relay server (full, simple path)

These commands run on a Linux VPS, for example an Ubuntu virtual machine on Google Cloud Compute Engine.

### 6.1 Copy relay/server.py to VPS

Example from your local machine:

```powershell
scp relay/server.py user@YOUR_VPS_IP:/home/user/appdesk-relay-server.py
```

### 6.2 SSH into VPS

```powershell
ssh user@YOUR_VPS_IP
```

### 6.3 Install Python (if needed)

```bash
sudo apt update
sudo apt install -y python3
```

### 6.4 Open firewall port 5902

If UFW is enabled:

```bash
sudo ufw allow 5902/tcp
sudo ufw reload
```

Also allow TCP port 5902 in the Google Cloud VPC firewall rule for the Relay Server virtual machine.

### 6.5 Run relay manually (first test)

```bash
python3 /home/user/appdesk-relay-server.py
```

You should see:

- Appdesk relay listening on :5902

Keep it running for test. Later press Ctrl+C to stop.

### 6.6 Run relay as a background service (recommended)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/appdesk-relay.service
```

Paste this:

```ini
[Unit]
Description=Appdesk Relay Server
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user
ExecStart=/usr/bin/python3 /home/user/appdesk-relay-server.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable appdesk-relay
sudo systemctl start appdesk-relay
sudo systemctl status appdesk-relay
```

View logs:

```bash
sudo journalctl -u appdesk-relay -f
```

## 7) Point Appdesk to your relay

Edit config.py and set:

```python
RELAY_SERVER = "YOUR_VPS_IP:5902"
```

Example:

```python
RELAY_SERVER = "203.0.113.10:5902"
```

## 8) Run Appdesk

On both host and client machines:

```powershell
python main.py
```

## 9) Host flow (exact clicks)

1. In Appdesk window, check Relay host:port value.
2. Click Start Hosting.
3. Wait for Session ID to appear.
4. Send Session ID to the other person.

For direct LAN mode, delete the value in the Relay host:port field and click Start Hosting. The hosting window shows the local IP address of the Host.

## 10) Client flow (exact clicks)

1. In Appdesk window, put same Relay host:port.
2. Paste Session ID from host.
3. Click Connect.
4. Remote screen window opens.
5. Mouse and keyboard control should now work.

For direct LAN mode, delete the value in the Relay host:port field, enter the Host local IP address instead of a Session ID, and click Connect.

## 11) Important behavior notes

- Session ID is generated by relay when host registers screen channel.
- Host input channel starts after session is ready.
- Client opens screen and input channels separately.
- UI shows FPS and approximate latency in remote session title.
- If network drops, session window closes and warns user.
- Direct LAN mode uses the Host local IP address and ports 5900 and 5901.

## 12) Method used in code (simple technical summary)

- Screen streaming method:
  - mss captures screen
  - Pillow resizes + JPEG compresses
  - raw packet: [size][jpeg][cursor len][cursor][timestamp]
- Input method:
  - JSON lines over TCP
  - event types: move, click, scroll, key
  - pynput executes events on host OS
- Relay method:
  - line-based JSON handshake first
  - then transparent byte piping between host and client

## 13) Quick command checklist

Local Windows setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Relay VPS setup summary:

```bash
sudo apt update
sudo apt install -y python3
python3 /home/user/appdesk-relay-server.py
```

Service commands summary:

```bash
sudo systemctl enable appdesk-relay
sudo systemctl start appdesk-relay
sudo systemctl status appdesk-relay
```

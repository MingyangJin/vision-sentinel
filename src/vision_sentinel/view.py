"""Development MJPEG viewer - a FrameBus consumer, not part of the pipeline."""

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vision_sentinel.recorder import FrameBus

SOI, EOI = b"\xff\xd8", b"\xff\xd9"
BOUNDARY = "vsframe"

PAGE = """<!doctype html><meta charset=utf-8><title>Vision Sentinel - {stream}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0b0d10; color:#c9d1d9;
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
         display:flex; flex-direction:column; align-items:center; gap:12px; padding:20px; }}
  img {{ max-width:100%; border-radius:8px; box-shadow:0 0 0 1px #21262d; background:#000; }}
  .meta {{ display:flex; gap:18px; color:#7d8590; }}
  b {{ color:#c9d1d9; font-weight:600; }}
</style>
<img src="/stream.mjpg" alt="live camera feed">
<div class=meta>
  <span>stream <b>{stream}</b></span><span>source <b>{size}</b></span><span>view <b>{fps} fps</b></span>
</div>
"""


def _pump(proc: subprocess.Popen, bus: FrameBus) -> None:
    """Split ffmpeg's MJPEG output into whole JPEG frames."""
    buf = b""
    while chunk := proc.stdout.read(65536):
        buf += chunk
        while True:
            start = buf.find(SOI)
            end = buf.find(EOI, start + 2) if start != -1 else -1
            if start == -1 or end == -1:
                break
            bus.publish(buf[start:end + 2])
            buf = buf[end + 2:]


def serve(rtsp_url: str, stream: str, size: str,
          bind: str, port: int, fps: int, quality: int) -> None:
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-rtsp_transport", "tcp", "-i", rtsp_url,
         "-an", "-r", str(fps), "-f", "mjpeg", "-q:v", str(quality), "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    bus = FrameBus()
    threading.Thread(target=_pump, args=(proc, bus), daemon=True).start()
    page = PAGE.format(stream=stream, size=size, fps=fps).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif self.path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type",
                                 f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.end_headers()
                seq = 0
                try:
                    while True:
                        frame, seq = bus.wait(seq)
                        if frame is None:
                            continue
                        self.wfile.write(
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                            f"Content-Length: {len(frame)}\r\n\r\n".encode())
                        self.wfile.write(frame + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404)

    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    print(f"  http://{bind}:{port}/   ({stream}, {size}, {fps}fps)  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        srv.server_close()

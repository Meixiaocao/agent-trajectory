# -*- coding: utf-8 -*-
"""
live_server.py — 实时跟踪模式本地服务器（轨迹回放页 v2.2）

用法: python -X utf8 live_server.py [端口=8765]
  浏览器打开 http://localhost:8765 → 页面里点「🔴 跟踪模式」

提供:
  /                    回放页(agent-trajectory-replay.html)
  /lib/*, /*           静态文件
  /api/sessions        会话清单(mtime倒序; live=120秒内有写入)
  /api/events?sid=<目录>/<文件>&off=<字节偏移>   增量解析新行→schema事件
会话根: ~/.pi/agent/sessions（环境变量 PI_TRAJ_SESS_ROOT 可覆盖，测试用）
"""
import http.server, json, os, subprocess, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_pi_session import parse_events
from build_library import project_name

ROOT = os.environ.get("PI_TRAJ_SESS_ROOT") or os.path.expanduser("~/.pi/agent/sessions")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = {}          # sid → {"pending":{},"seq":N} 跨请求续接(工具调用/结果跨批次配对)
LIVE_WIN = 120      # 秒内有写入=直播中

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=HERE, **k)
    def log_message(self, *a): pass
    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            self.path = "/agent-trajectory-replay.html"   # 根路径=回放页
        if u.path == "/open":
            q = urllib.parse.parse_qs(u.query)
            fp = os.path.normpath(q.get("path", [""])[0])
            if fp and os.path.exists(fp):
                try:
                    if sys.platform.startswith("win"): os.startfile(fp)   # noqa: 用系统默认程序打开
                    elif sys.platform == "darwin": subprocess.Popen(["open", fp])
                    else: subprocess.Popen(["xdg-open", fp])
                    self.send_response(204); self.end_headers()
                except OSError as ex: self._json({"error": str(ex)})
            else: self._json({"error": "not found: " + fp})
            return
        if u.path == "/api/sessions":
            out = []
            if os.path.isdir(ROOT):
                for folder in os.listdir(ROOT):
                    fd = os.path.join(ROOT, folder)
                    if not os.path.isdir(fd) or "backup" in folder or ".migrated" in folder: continue
                    for f in os.listdir(fd):
                        if not f.endswith(".jsonl"): continue
                        try: st = os.stat(os.path.join(fd, f))
                        except OSError: continue
                        out.append({"sid": folder + "/" + f, "proj": project_name(folder), "file": f,
                                    "mtime": st.st_mtime, "size": st.st_size,
                                    "live": time.time() - st.st_mtime < LIVE_WIN})
            out.sort(key=lambda x: -x["mtime"])
            self._json({"sessions": out[:80]}); return
        if u.path == "/api/events":
            q = urllib.parse.parse_qs(u.query)
            sid = q.get("sid", [""])[0]
            try: off = int(q.get("off", ["0"])[0])
            except ValueError: off = 0
            fp = os.path.normpath(os.path.join(ROOT, sid))
            if not fp.startswith(os.path.normpath(ROOT)) or not os.path.isfile(fp):
                self._json({"error": "bad sid"}); return
            st = STATE.setdefault(sid, {"pending": {}, "seq": 0})
            size = os.path.getsize(fp)
            with open(fp, "rb") as fh:
                fh.seek(min(max(off, 0), size)); raw = fh.read()
            cut = raw.rfind(b"\n") + 1                      # 只取完整行,残行留给下次
            lines = raw[:cut].decode("utf-8", errors="replace").splitlines()
            evs = parse_events(lines, state=st) if lines else []
            self._json({"events": evs, "off": off + cut, "end": off + cut >= size}); return
        return super().do_GET()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"跟踪服务器: http://localhost:{port}   会话根={ROOT}")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

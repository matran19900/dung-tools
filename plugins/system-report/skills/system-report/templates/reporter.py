# =============================================================================
# system-report Reporter — TELEMETRY TUỲ CHỌN, MẶC ĐỊNH TẮT.
#
#   Gỡ bỏ hoàn toàn = xoá file này + 1 dòng wiring (`report.install()`). Hết. Không dấu vết.
#
# Bật:  REPORT_ENABLED=true  REPORT_INSTANCE={{INSTANCE}}  REPORT_REDIS_HOST=...
# Wiring (đúng 1 dòng, đặt ngay sau khi cấu hình logging xong):
#       import report; report.install()
#
# BẤT BIẾN (system-report §3.4):
#   · airtight  — nuốt MỌI exception, không bao giờ raise/blocking vào caller
#   · opt-in    — REPORT_ENABLED chưa bật thì install() là no-op tuyệt đối
#   · 1 file    — zero dependency, nói RESP thẳng qua TCP socket
#   · drop-safe — hàng đợi có trần; đầy thì VỨT log, không bao giờ chặn app
#   · chỉ ghi namespace REPORT_* trên 1 Redis-log RIÊNG (không phải Redis production)
# =============================================================================
import atexit
import logging
import os
import queue
import socket
import threading
import time
from datetime import datetime

_ENABLED = os.getenv("REPORT_ENABLED", "false").lower() in ("1", "true", "yes")
_INSTANCE = os.getenv("REPORT_INSTANCE") or socket.gethostname()
_HOST = os.getenv("REPORT_REDIS_HOST", "127.0.0.1")
_PORT = int(os.getenv("REPORT_REDIS_PORT", "6399") or 6399)
_DB = os.getenv("REPORT_REDIS_DB", "0")
_PASS = os.getenv("REPORT_REDIS_PASS", "")
_TTL = int(os.getenv("REPORT_TTL_DAYS", "7") or 7) * 86400
_FLUSH_SEC = float(os.getenv("REPORT_FLUSH_SEC", "5") or 5)
_QUEUE_MAX = int(os.getenv("REPORT_QUEUE_MAX", "2000") or 2000)
_LEVEL = os.getenv("REPORT_LEVEL", "WARNING").upper()

_q: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
_dropped = 0
_started = False
_lock = threading.Lock()


# ── RESP tối giản (không cần thư viện redis) ─────────────────────────────────
class _Conn:
    def __init__(self):
        self.sock = None
        self.rf = None

    def connect(self):
        self.close()
        s = socket.create_connection((_HOST, _PORT), timeout=5)
        s.settimeout(5)
        self.sock, self.rf = s, s.makefile("rb")
        if _PASS:
            self.cmd("AUTH", _PASS)
        if _DB and _DB != "0":
            self.cmd("SELECT", _DB)

    def cmd(self, *args):
        buf = bytearray(b"*%d\r\n" % len(args))
        for a in args:
            b = a.encode("utf-8", "replace") if isinstance(a, str) else a
            buf += b"$%d\r\n" % len(b) + b + b"\r\n"
        self.sock.sendall(bytes(buf))
        return self._reply()

    def _reply(self):
        line = self.rf.readline()
        if not line:
            raise IOError("redis closed")
        tag, body = line[:1], line[1:-2]
        if tag in b"+:-":
            return body
        if tag == b"$":
            n = int(body)
            if n < 0:
                return None
            data = self.rf.read(n + 2)
            return data[:-2]
        if tag == b"*":
            return [self._reply() for _ in range(max(0, int(body)))]
        return body

    def close(self):
        try:
            if self.rf:
                self.rf.close()
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = self.rf = None


def _key():
    return "REPORT_DAILY:%s:%s" % (datetime.now().strftime("%Y-%m-%d"), _INSTANCE)


def _worker():
    conn, registered = _Conn(), False
    while True:
        try:
            first = _q.get()  # chặn worker (thread nền), KHÔNG chặn app
        except Exception:
            return
        batch = [first]
        deadline = time.monotonic() + _FLUSH_SEC
        while len(batch) < 200:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                batch.append(_q.get(timeout=left))
            except Exception:
                break
        try:
            if conn.sock is None:
                conn.connect()
                registered = False
            if not registered:
                conn.cmd("SADD", "REPORT_INSTANCES", _INSTANCE)
                registered = True
            payload = "\n".join(batch) + "\n"
            k = _key()
            conn.cmd("APPEND", k, payload)
            conn.cmd("EXPIRE", k, str(_TTL))
        except Exception:
            conn.close()  # lần sau tự kết nối lại; log của batch này bị bỏ — chấp nhận
        finally:
            for _ in batch:
                try:
                    _q.task_done()
                except Exception:
                    pass


def log(line: str) -> None:
    """Đẩy 1 dòng vào báo cáo ngày. Không bao giờ raise, không bao giờ block."""
    global _dropped
    if not _ENABLED:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        _q.put_nowait("%s [%s] %s" % (ts, _INSTANCE, str(line).replace("\r", " ")))
    except queue.Full:
        _dropped += 1
    except Exception:
        pass


class _Handler(logging.Handler):
    def emit(self, record):
        try:
            log(self.format(record))
        except Exception:
            pass


def install() -> None:
    """1 dòng wiring duy nhất. Chưa bật REPORT_ENABLED → no-op tuyệt đối."""
    global _started
    if not _ENABLED:
        return
    try:
        with _lock:
            if _started:
                return
            _started = True
        t = threading.Thread(target=_worker, name="system-report", daemon=True)
        t.start()
        h = _Handler(level=getattr(logging, _LEVEL, logging.WARNING))
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(h)
        atexit.register(lambda: log("instance stopping (dropped=%d)" % _dropped))
        log("instance started")
    except Exception:
        pass  # telemetry hỏng KHÔNG được ảnh hưởng hệ thống

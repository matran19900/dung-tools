// =============================================================================
// system-report Reporter — TELEMETRY TUỲ CHỌN, MẶC ĐỊNH TẮT.
//
//   Gỡ bỏ hoàn toàn = xoá file này + 1 dòng wiring (`installReporter()`). Hết.
//
// Bật:  REPORT_ENABLED=true  REPORT_INSTANCE={{INSTANCE}}  REPORT_REDIS_HOST=...
// Wiring (đúng 1 dòng, đặt sớm nhất trong entrypoint):
//       import { installReporter } from "./reporter"; installReporter();
//
// BẤT BIẾN (system-report §3.4):
//   · airtight  — nuốt MỌI exception; không throw, không await, không chặn event loop
//   · opt-in    — REPORT_ENABLED chưa bật thì installReporter() là no-op tuyệt đối
//   · 1 file    — zero dependency, nói RESP thẳng qua node:net
//   · drop-safe — hàng đợi có trần; đầy thì VỨT log
//   · chỉ ghi namespace REPORT_* trên 1 Redis-log RIÊNG (không phải Redis production)
// =============================================================================
import net from "node:net";
import os from "node:os";

const env = process.env;
const ENABLED = /^(1|true|yes)$/i.test(env.REPORT_ENABLED ?? "false");
const INSTANCE = env.REPORT_INSTANCE || os.hostname();
const HOST = env.REPORT_REDIS_HOST || "127.0.0.1";
const PORT = Number(env.REPORT_REDIS_PORT || 6399);
const DB = env.REPORT_REDIS_DB || "0";
const PASS = env.REPORT_REDIS_PASS || "";
const TTL = Number(env.REPORT_TTL_DAYS || 7) * 86400;
const FLUSH_MS = Number(env.REPORT_FLUSH_SEC || 5) * 1000;
const QUEUE_MAX = Number(env.REPORT_QUEUE_MAX || 2000);

let queue: string[] = [];
let dropped = 0;
let started = false;
let sock: net.Socket | null = null;
let connecting = false;
let registered = false;

function resp(...args: string[]): Buffer {
  const parts = [Buffer.from(`*${args.length}\r\n`)];
  for (const a of args) {
    const b = Buffer.from(String(a), "utf8");
    parts.push(Buffer.from(`$${b.length}\r\n`), b, Buffer.from("\r\n"));
  }
  return Buffer.concat(parts);
}

function drop(): void {
  try {
    sock?.destroy();
  } catch {
    /* ignore */
  }
  sock = null;
  registered = false;
}

function connect(): void {
  if (sock || connecting) return;
  connecting = true;
  try {
    const s = net.createConnection({ host: HOST, port: PORT });
    s.setNoDelay(true);
    s.unref(); // không giữ process sống chỉ vì telemetry
    s.on("connect", () => {
      connecting = false;
      sock = s;
      try {
        if (PASS) s.write(resp("AUTH", PASS));
        if (DB !== "0") s.write(resp("SELECT", DB));
      } catch {
        drop();
      }
    });
    s.on("data", () => {
      /* nuốt reply, tránh phình buffer */
    });
    s.on("error", () => {
      connecting = false;
      drop();
    });
    s.on("close", () => {
      connecting = false;
      drop();
    });
  } catch {
    connecting = false;
    drop();
  }
}

function today(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function flush(): void {
  if (queue.length === 0) return;
  connect();
  if (!sock || !sock.writable) return; // giữ hàng đợi, thử lại lượt sau
  const batch = queue;
  queue = [];
  try {
    const key = `REPORT_DAILY:${today()}:${INSTANCE}`;
    if (!registered) {
      sock.write(resp("SADD", "REPORT_INSTANCES", INSTANCE));
      registered = true;
    }
    sock.write(resp("APPEND", key, batch.join("\n") + "\n"));
    sock.write(resp("EXPIRE", key, String(TTL)));
  } catch {
    drop(); // batch này mất — chấp nhận, telemetry không được làm phiền app
  }
}

/** Đẩy 1 dòng vào báo cáo ngày. Không bao giờ throw, không bao giờ block. */
export function report(line: string): void {
  if (!ENABLED) return;
  try {
    if (queue.length >= QUEUE_MAX) {
      dropped++;
      return;
    }
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, "0");
    const t = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; // giờ LOCAL, khớp key ngày
    queue.push(`${t} [${INSTANCE}] ${String(line).replace(/\r/g, " ")}`);
  } catch {
    /* ignore */
  }
}

/** 1 dòng wiring duy nhất. Chưa bật REPORT_ENABLED → no-op tuyệt đối. */
export function installReporter(): void {
  if (!ENABLED || started) return;
  started = true;
  try {
    const timer = setInterval(flush, FLUSH_MS);
    timer.unref();

    for (const level of ["error", "warn"] as const) {
      const orig = console[level].bind(console);
      console[level] = (...a: unknown[]) => {
        report(`${level.toUpperCase()} ${a.map(String).join(" ")}`);
        orig(...(a as []));
      };
    }
    process.on("uncaughtException", (e) => report(`FATAL uncaughtException: ${e?.stack || e}`));
    process.on("unhandledRejection", (e) => report(`FATAL unhandledRejection: ${String(e)}`));
    process.on("exit", () => {
      report(`instance stopping (dropped=${dropped})`);
      flush();
    });
    report("instance started");
    flush();
  } catch {
    /* telemetry hỏng KHÔNG được ảnh hưởng hệ thống */
  }
}

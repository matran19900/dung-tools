#!/usr/bin/env python3
"""Đo chi phí token của các phiên Claude Code trong MỘT project.

Chạy được ở bất kỳ project nào, không cần tham số: script tự suy ra thư mục
transcript từ thư mục làm việc hiện tại (Claude Code mã hoá đường dẫn cwd
thành tên thư mục trong ~/.claude/projects/ bằng cách đổi "/" và "_" thành "-").

    python3 session-cost.py              # project hiện tại, toàn bộ lịch sử
    python3 session-cost.py --days 7     # CHỈ tính các lượt trong 7 ngày gần nhất
    python3 session-cost.py --all        # mọi project trên máy
    python3 session-cost.py --project /duong/dan/khac
    python3 session-cost.py --top 10     # chỉ in N phiên đắt nhất

``--days N`` lọc ở mức TỪNG LƯỢT theo timestamp, không phải mức file: một phiên
mở 20 ngày chỉ được tính phần lượt nằm trong cửa sổ. Đó là con số đúng cho câu
hỏi "đã tiêu bao nhiêu trong N ngày qua".

MỌI SỐ TOKEN ĐỀU LÀ SỐ THẬT: transcript ghi sẵn ``message.usage`` cho từng
lượt (input / cache_creation / cache_read / output). Script cộng dồn đúng các
con số đó — không ước lượng gì.

Chỉ ĐƠN GIÁ là giả định (sửa ở khối hằng số bên dưới cho khớp bảng giá đang
áp dụng). Vì vậy sai số nếu có sẽ dịch chuyển MỌI phiên cùng một tỉ lệ, không
làm đổi thứ hạng.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- ĐƠN GIÁ (thứ duy nhất là giả định) — USD / triệu token, mức opus ----
USD_PER_M_INPUT = 15.00
USD_PER_M_CACHE_WRITE = 18.75
USD_PER_M_CACHE_READ = 1.50
USD_PER_M_OUTPUT = 75.00
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".sh", ".go", ".rs",
            ".java", ".rb", ".c", ".cpp", ".h", ".sql", ".ps1"}


def projects_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def encode_cwd(path: Path) -> str:
    """Claude Code đổi "/" và "_" trong đường dẫn thành "-"."""
    return str(path).replace("/", "-").replace("_", "-")


def find_project_dir(cwd: Path) -> Path | None:
    root = projects_root()
    if not root.is_dir():
        return None
    exact = root / encode_cwd(cwd)
    if exact.is_dir():
        return exact
    # dự phòng: khớp theo tên thư mục cuối, chọn cái nhiều dữ liệu nhất
    leaf = cwd.name.replace("_", "-")
    cands = [d for d in root.iterdir() if d.is_dir() and leaf in d.name]
    if not cands:
        return None
    return max(cands, key=lambda d: sum(f.stat().st_size for f in d.glob("*.jsonl")))


def scan_usage_only(path: Path, since: str | None = None) -> float:
    """Chi phí THẬT của một transcript subagent (chỉ đọc usage)."""
    tot = 0.0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if since and (rec.get("timestamp") or "") < since:
                    continue
                msg = rec.get("message")
                if not (isinstance(msg, dict) and msg.get("role") == "assistant"):
                    continue
                u = msg.get("usage") or {}
                if not u:
                    continue
                tot += (u.get("input_tokens", 0) * USD_PER_M_INPUT
                        + u.get("cache_creation_input_tokens", 0) * USD_PER_M_CACHE_WRITE
                        + u.get("cache_read_input_tokens", 0) * USD_PER_M_CACHE_READ
                        + u.get("output_tokens", 0) * USD_PER_M_OUTPUT) / 1e6
    except OSError:
        return 0.0
    return tot


def scan_session(path: Path, since: str | None = None) -> dict:
    turns = compacts = agents = md = code = 0
    t_in = t_cw = t_cr = t_out = 0
    ctx_first = 0
    roles: collections.Counter[str] = collections.Counter()
    models: collections.Counter[str] = collections.Counter()
    agent_topics: set[str] = set()
    first = last = None

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("timestamp")
            out_of_window = bool(since and ts and ts < since)
            # Vai trò (/roles:) luôn quét CẢ file: marker nằm ở đầu phiên, thường
            # ngoài cửa sổ --days, lọc nó đi sẽ báo sai vai.
            msg_probe = rec.get("message")
            if isinstance(msg_probe, dict):
                cprobe = msg_probe.get("content")
                texts = ([cprobe] if isinstance(cprobe, str) else
                         [str(b.get("text", "")) for b in cprobe
                          if isinstance(b, dict) and b.get("type") == "text"]
                         if isinstance(cprobe, list) else [])
                for t in texts:
                    for r in ("roles:cto", "roles:em"):
                        if r in t:
                            roles[r] += 1
            if out_of_window:
                continue        # --days: bỏ qua lượt ngoài cửa sổ
            if ts:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
            if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
                compacts += 1
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                turns += 1
                u = msg.get("usage") or {}
                if u:
                    i_, cw, cr, o_ = (u.get("input_tokens", 0),
                                      u.get("cache_creation_input_tokens", 0),
                                      u.get("cache_read_input_tokens", 0),
                                      u.get("output_tokens", 0))
                    t_in += i_; t_cw += cw; t_cr += cr; t_out += o_
                    if not ctx_first:
                        ctx_first = i_ + cw + cr
            if msg.get("model"):
                models[msg["model"]] += 1
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") != "tool_use":
                    continue
                name, inp = blk.get("name"), blk.get("input") or {}
                if name in ("Edit", "Write"):
                    ext = os.path.splitext(str(inp.get("file_path", "")))[1]
                    if ext == ".md":
                        md += 1
                    elif ext in CODE_EXT:
                        code += 1
                elif name == "Agent":
                    agents += 1
                    agent_topics.add(str(inp.get("description", "")))

    mb = path.stat().st_size / 1048576
    usd = (t_in * USD_PER_M_INPUT + t_cw * USD_PER_M_CACHE_WRITE
           + t_cr * USD_PER_M_CACHE_READ + t_out * USD_PER_M_OUTPUT) / 1e6
    avg_ctx = (t_in + t_cw + t_cr) / turns if turns else 0

    # Subagent của CHÍNH phiên này nằm ở <uuid>/subagents/. Context của chúng
    # nhỏ và riêng biệt nên chi phí gần TUYẾN TÍNH theo nội dung — khác hẳn
    # phiên chính (bình phương theo độ dài).
    sub_dir = path.with_suffix("") / "subagents"
    sub_files = list(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else []
    sub_mb = sum(f.stat().st_size for f in sub_files) / 1048576
    sub_usd = sum(scan_usage_only(f, since) for f in sub_files)

    if roles.get("roles:cto", 0) > roles.get("roles:em", 0):
        role = "CTO"
    elif roles.get("roles:em", 0) > 0:
        role = "EM"
    elif md + code:
        role = "CTO?" if md / (md + code) > 0.6 else "EM?"
    else:
        role = "-"

    return {
        "id": path.stem[:8], "mb": mb, "turns": turns, "compacts": compacts,
        "agents": agents, "topics": len(agent_topics), "role": role,
        "usd": usd, "sub_usd": sub_usd, "sub_mb": sub_mb, "sub_n": len(sub_files),
        "avg_ctx": avg_ctx, "ctx_first": ctx_first, "cr": t_cr, "out": t_out,
        "start": (first or "?")[:10], "end": (last or "?")[:10],
        "model": models.most_common(1)[0][0] if models else "-",
    }


def report(pdir: Path, top: int, since: str | None = None) -> float:
    mains = sorted((f for f in pdir.glob("*.jsonl") if not f.name.startswith("agent-")),
                   key=lambda f: -f.stat().st_size)
    if not mains:
        print(f"  (không có transcript trong {pdir})")
        return 0.0

    rows = [scan_session(f, since) for f in mains]
    rows = [r for r in rows if r["turns"]]
    for r in rows:
        r["all_usd"] = r["usd"] + r["sub_usd"]
    rows.sort(key=lambda r: -r["all_usd"])
    total = sum(r["all_usd"] for r in rows)
    sub_total = sum(r["sub_usd"] for r in rows)
    sub_n = sum(r["sub_n"] for r in rows)

    print(f"\n  {'phiên':10}{'vai':6}{'lượt':>7}{'nén':>5}{'ctxTB':>8}{'spawn':>7}"
          f"{'chính$':>8}{'sub$':>7}{'TỔNG$':>8}{'%':>5}  khoảng ngày")
    print("  " + "-" * 94)
    for r in rows[:top]:
        ag = f"{r['agents']}/{r['topics']}" if r["agents"] else "-"
        pct = r["all_usd"] / total * 100 if total else 0
        print(f"  {r['id']:10}{r['role']:6}{r['turns']:7d}{r['compacts']:5d}"
              f"{r['avg_ctx'] / 1000:7.0f}k{ag:>7}{r['usd']:8.0f}{r['sub_usd']:7.0f}"
              f"{r['all_usd']:8.0f}{pct:5.0f}%  {r['start']} → {r['end']}")
    if len(rows) > top:
        rest = sum(r["all_usd"] for r in rows[top:])
        print(f"  {'… ' + str(len(rows) - top) + ' phiên còn lại':30}"
              f"{'':31}{rest:8.0f}{rest / total * 100:5.0f}%")

    print("  " + "-" * 94)
    print(f"  TỔNG: ${total:,.0f} / {len(rows)} phiên"
          f"   ·   trong đó subagent ~${sub_total:,.0f} ({sub_n} lượt,"
          f" {sub_total / total * 100 if total else 0:.0f}%)")

    # cảnh báo: phiên nào đang phình
    flags = [r for r in rows if r["compacts"] >= 2 or r["start"] != r["end"]]
    worst = rows[0]
    if worst["all_usd"] > 0 and worst["all_usd"] / total > 0.35:
        print(f"\n  ⚠ {worst['id']} chiếm {worst['all_usd'] / total * 100:.0f}% chi phí project"
              f" — {worst['turns']} lượt, {worst['compacts']} lần nén,"
              f" trải {worst['start']} → {worst['end']}.")
        if since is None:
            print(f"    Context TB {worst['avg_ctx'] / 1000:.0f}k/lượt so với sàn"
                  f" {worst['ctx_first'] / 1000:.0f}k của một phiên mới"
                  f" — mỗi lượt đắt gấp {worst['avg_ctx'] / max(worst['ctx_first'], 1):.1f}×.")
        else:
            print(f"    Context TB {worst['avg_ctx'] / 1000:.0f}k/lượt trong cửa sổ này.")
        print("    Phiên đa-ngày là dấu hiệu bị --resume nhiều lần. Chốt memory rồi /clear.")
    for r in flags:
        if r["agents"] and r["topics"] and r["agents"] / r["topics"] >= 2:
            print(f"  ⚠ {r['id']}: {r['agents']} lần spawn cho {r['topics']} chủ đề"
                  f" (lặp {r['agents'] / r['topics']:.1f}×) — nghiên cứu bị làm lại do mất context.")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Đo chi phí phiên Claude Code")
    ap.add_argument("--project", help="đường dẫn project (mặc định: thư mục hiện tại)")
    ap.add_argument("--all", action="store_true", help="quét mọi project trên máy")
    ap.add_argument("--top", type=int, default=12, help="in N phiên đắt nhất (mặc định 12)")
    ap.add_argument("--days", type=int, help="chỉ tính các LƯỢT trong N ngày gần nhất")
    args = ap.parse_args()

    root = projects_root()
    if not root.is_dir():
        print(f"Không tìm thấy {root}", file=sys.stderr)
        return 1

    since = None
    if args.days:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        print(f"[cửa sổ: {args.days} ngày gần nhất — chỉ tính lượt từ {since[:10]}]")

    if args.all:
        grand = 0.0
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            print(f"\n=== {d.name} ===")
            grand += report(d, args.top, since)
        print(f"\n=== TỔNG MỌI PROJECT: ~${grand:,.0f} ===")
        return 0

    cwd = Path(args.project).resolve() if args.project else Path.cwd()
    pdir = find_project_dir(cwd)
    if pdir is None:
        print(f"Không tìm thấy transcript cho {cwd} trong {root}.", file=sys.stderr)
        print("Thử: python3 session-cost.py --all", file=sys.stderr)
        return 1
    print(f"=== {cwd}  →  {pdir.name} ===")
    report(pdir, args.top, since)
    print("\n  Token là SỐ THẬT (đọc message.usage của từng lượt). Chỉ đơn giá là giả định:")
    print(f"  ${USD_PER_M_INPUT:.0f}/M input · ${USD_PER_M_CACHE_WRITE}/M ghi cache ·"
          f" ${USD_PER_M_CACHE_READ}/M đọc cache · ${USD_PER_M_OUTPUT:.0f}/M output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

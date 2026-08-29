#!/usr/bin/env python3
"""Đo chi phí token của các phiên Claude Code trong MỘT project.

Chạy được ở bất kỳ project nào, không cần tham số: script tự suy ra thư mục
transcript từ thư mục làm việc hiện tại (Claude Code mã hoá đường dẫn cwd
thành tên thư mục trong ~/.claude/projects/ bằng cách đổi "/", "\\", ":" và "_" thành "-").

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

# ---- ĐƠN GIÁ (thứ duy nhất là giả định) — USD / triệu token, mức Claude Opus 5 ----
# Ghi cache lấy giá TTL 1h = 2x input (phiên Claude Code dùng TTL 1h). Nếu một phần
# ghi thực tế ở TTL 5m (1.25x) thì phần đó bị tính dôi lên. Đọc cache = 0.1x input.
USD_PER_M_INPUT = 5.00
USD_PER_M_CACHE_WRITE = 10.00
USD_PER_M_CACHE_READ = 0.50
USD_PER_M_OUTPUT = 25.00
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".sh", ".go", ".rs",
            ".java", ".rb", ".c", ".cpp", ".h", ".sql", ".ps1"}


def projects_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def encode_cwd(path: Path) -> str:
    r"""Claude Code đổi "/", "\\", ":" và "_" trong đường dẫn thành "-".

    Trên Windows PHẢI đổi cả "\\" và ":": nếu để nguyên, `root / "E:\Dev\x"`
    bị pathlib coi là đường tuyệt đối có ổ đĩa nên **nuốt mất root** — hàm dưới
    khi đó trỏ vào chính thư mục project (không có .jsonl) và báo "không có
    transcript", thay vì đọc ~/.claude/projects/e--Dev-x.
    """
    return (str(path).replace("/", "-").replace("\\", "-")
            .replace(":", "-").replace("_", "-"))


def find_project_dir(cwd: Path) -> Path | None:
    root = projects_root()
    if not root.is_dir():
        return None
    enc = encode_cwd(cwd)
    dirs = [d for d in root.iterdir() if d.is_dir()]
    # Quét thư mục thay vì `(root / enc).is_dir()`: trên NTFS phép thử đó khớp
    # bất kể hoa/thường và trả về Path mang tên ta tự dựng, nên dòng
    # "=== ... → ... ===" in ra một tên KHÔNG tồn tại trên đĩa.
    for d in dirs:                                   # khớp chính xác trước —
        if d.name == enc:                            # POSIX (/home/x ≠ /Home/x)
            return d                                 # nhờ vậy không bị nhận nhầm
    low = enc.lower()                                # rồi mới bỏ qua hoa/thường:
    for d in dirs:                                   # cwd "E:\..." → "E--Dev-x"
        if d.name.lower() == low:                    # còn thư mục thật "e--Dev-x"
            return d
    # dự phòng: khớp theo tên thư mục cuối, chọn cái nhiều dữ liệu nhất
    leaf = cwd.name.replace("_", "-")
    cands = [d for d in dirs if leaf in d.name]
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
    # Tách chi phí phiên chính thành 2 khoản, KHÔNG đổi công thức:
    # in_usd + out_usd == usd (bất biến, cột in$ + out$ phải bằng cột chính$).
    in_tok = t_in + t_cw + t_cr        # gộp cả 3: gần như toàn bộ input đi qua cache,
    out_tok = t_out                    # lấy riêng input_tokens thô sẽ ra ~0 và trông như bug
    in_usd = (t_in * USD_PER_M_INPUT + t_cw * USD_PER_M_CACHE_WRITE
              + t_cr * USD_PER_M_CACHE_READ) / 1e6
    out_usd = t_out * USD_PER_M_OUTPUT / 1e6
    usd = in_usd + out_usd
    avg_ctx = in_tok / turns if turns else 0

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
        "in_tok": in_tok, "out_tok": out_tok, "in_usd": in_usd, "out_usd": out_usd,
        "avg_ctx": avg_ctx, "ctx_first": ctx_first, "cr": t_cr, "out": t_out,
        "start": (first or "?")[:10], "end": (last or "?")[:10],
        "model": models.most_common(1)[0][0] if models else "-",
    }


def tok(n: float) -> str:
    """661_700_000 → '661.7M' · 2_000_000 → '2.0M' · 15_400 → '15k'."""
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


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

    # `%` và thứ tự sort vẫn dựa trên all_usd (chính$ + sub$) — cột TỔNG$ chỉ bị
    # BỎ KHỎI BẢNG, không bỏ khỏi logic; đổi sang chính$ sẽ làm đổi thứ hạng phiên.
    head = (f"  {'phiên':9}{'vai':5}{'lượt':>6}{'ctxTB':>8}{'spawn':>7}"
            f"{'in-tok':>9}{'in$':>8}{'out-tok':>9}{'out$':>7}"
            f"{'chính$':>9}{'sub$':>7}{'%':>6}  khoảng ngày")
    rule = "  " + "-" * (len(head) + 21)

    # $ hiển thị là số nguyên. Hai bất biến của bảng, cả hai đều về LÀM TRÒN:
    #  (1) trong một hàng: in$ + out$ == chính$  → chốt chính$ và in$, DẪN XUẤT out$.
    #  (2) cột dọc: row TỔNG == tổng các hàng ĐANG HIỂN THỊ → cộng số đã làm tròn,
    #      không làm tròn lại tổng thật (lệch $1-2 trông như bug khi CEO cộng tay).
    # Số thật vẫn được giữ nguyên cho %, thứ tự sort, cảnh báo ⚠ và tổng mọi project.
    def money(r):
        usd_d, in_d = round(r["usd"]), round(r["in_usd"])
        return in_d, usd_d - in_d, usd_d, round(r["sub_usd"])

    def line(idc, role, turns, ctx, ag, itk, in_d, otk, out_d, usd_d, sub_d, pct, span):
        return (f"  {idc:9}{role:5}{turns:6d}{ctx / 1000:7.0f}k{ag:>7}"
                f"{tok(itk):>9}{in_d:8d}{tok(otk):>9}{out_d:7d}"
                f"{usd_d:9d}{sub_d:7d}{pct:5.0f}%  {span}")

    acc = dict(turns=0, in_tok=0, out_tok=0, in_d=0, out_d=0, usd_d=0, sub_d=0,
               agents=0, topics=0)

    def take(r_turns, r_in_tok, r_out_tok, m, agents, topics):
        acc["turns"] += r_turns; acc["in_tok"] += r_in_tok; acc["out_tok"] += r_out_tok
        acc["in_d"] += m[0]; acc["out_d"] += m[1]; acc["usd_d"] += m[2]; acc["sub_d"] += m[3]
        acc["agents"] += agents; acc["topics"] += topics

    print()
    print(head)
    print(rule)
    for r in rows[:top]:
        m = money(r)
        take(r["turns"], r["in_tok"], r["out_tok"], m, r["agents"], r["topics"])
        print(line(r["id"], r["role"], r["turns"], r["avg_ctx"],
                   f"{r['agents']}/{r['topics']}" if r["agents"] else "-",
                   r["in_tok"], m[0], r["out_tok"], m[1], m[2], m[3],
                   r["all_usd"] / total * 100 if total else 0,
                   f"{r['start']} → {r['end']}"))
    if len(rows) > top:
        t = rows[top:]
        agg = {k: sum(x[k] for x in t) for k in
               ("turns", "in_tok", "out_tok", "usd", "in_usd", "sub_usd", "all_usd")}
        m = money(agg)
        take(agg["turns"], agg["in_tok"], agg["out_tok"], m,
             sum(x["agents"] for x in t), sum(x["topics"] for x in t))
        print(line(f"… {len(t)} ph.", "", agg["turns"],
                   agg["in_tok"] / max(agg["turns"], 1),
                   f"{sum(x['agents'] for x in t)}/{sum(x['topics'] for x in t)}",
                   agg["in_tok"], m[0], agg["out_tok"], m[1], m[2], m[3],
                   agg["all_usd"] / total * 100 if total else 0,
                   "(các phiên còn lại)"))

    # Row TỔNG: mọi cột CỘNG DỒN — RIÊNG ctxTB là trung bình có TRỌNG SỐ
    # (tổng in-tok / tổng lượt), KHÔNG phải cộng ctxTB của từng phiên.
    print(rule)
    print(line("TỔNG", "", acc["turns"], acc["in_tok"] / max(acc["turns"], 1),
               f"{acc['agents']}/{acc['topics']}" if acc["agents"] else "-",
               acc["in_tok"], acc["in_d"], acc["out_tok"], acc["out_d"],
               acc["usd_d"], acc["sub_d"], 100.0,
               f"{min(r['start'] for r in rows)} → {max(r['end'] for r in rows)}"
               f"  ({len(rows)} phiên, {sub_n} subagent)"))

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
    print(f"  ${USD_PER_M_INPUT:.0f}/M input · ${USD_PER_M_CACHE_WRITE:.0f}/M ghi cache ·"
          f" ${USD_PER_M_CACHE_READ}/M đọc cache · ${USD_PER_M_OUTPUT:.0f}/M output"
          f" (mức Claude Opus 5).")
    print("  Giả định: MỌI ghi cache tính giá TTL 1h (2x input); phần nào thực tế là"
          " TTL 5m (1.25x) sẽ bị tính dôi lên.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

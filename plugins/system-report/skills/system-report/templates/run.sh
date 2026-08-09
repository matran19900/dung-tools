#!/usr/bin/env bash
# ==============================================================================
# system-report runner — GENERIC, không gắn dự án nào.
# Đặc thù dự án nằm trong config.yml cạnh file này. Đừng sửa file này cho 1 dự án;
# nếu thiếu tính năng → sửa template trong skill rồi copy lại.
#
#   Vòng chạy: gom log đa-instance → AI triage READ-ONLY → giao digest + file dated.
#
#   ./run.sh                 chạy thật cho hôm nay
#   ./run.sh --date 2026-08-01   chạy bù 1 ngày
#   ./run.sh --dry-run       gom log + build payload, KHÔNG gọi AI, KHÔNG gửi webhook
#   ./run.sh --check         kiểm tra prereq + config rồi thoát
#   ./run.sh --status        in độ sẵn sàng (instance/cron/delivery/watchlist/last-run)
#   ./run.sh --no-webhook    chạy thật nhưng chỉ ghi file
#
# Exit code (cho cron):
#   0 ok · 1 config/prereq · 2 transport · 3 triage fail/timeout · 4 webhook fail
#
# GUARDRAIL: script này CHỈ ghi trong {file_dir}, WATCHLIST và .last-run.
# Nó KHÔNG sửa/commit/deploy code sản phẩm. Phiên AI chạy read-only, không tự ghi file.
# ==============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SELF_DIR/config.yml"
DATE="$(date +%F)"
MODE="run"
SEND_WEBHOOK=1

while [ $# -gt 0 ]; do
  case "$1" in
    --config)     CONFIG="$2"; shift 2 ;;
    --date)       DATE="$2"; shift 2 ;;
    --dry-run)    MODE="dry"; SEND_WEBHOOK=0; shift ;;
    --check)      MODE="check"; shift ;;
    --status)     MODE="status"; shift ;;
    --no-webhook) SEND_WEBHOOK=0; shift ;;
    -h|--help)    sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "run.sh: tham số lạ: $1" >&2; exit 1 ;;
  esac
done

log()  { printf '[system-report %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { log "FATAL: $*"; exit "${2:-1}"; }

[ -f "$CONFIG" ] || die "không thấy config: $CONFIG (chạy /system-report:init trước)" 1
command -v python3 >/dev/null 2>&1 || die "cần python3 để đọc config.yml" 1

WORK="$(mktemp -d "${TMPDIR:-/tmp}/system-report.XXXXXX")" || die "không tạo được thư mục tạm" 1
trap 'rm -rf "$WORK"' EXIT

# ── Đọc config (PyYAML nếu có, không thì parser subset stdlib) ────────────────
cat >"$WORK/cfg.py" <<'PYEOF'
import json, sys

def _split_top(s):
    out, buf, depth, q = [], [], 0, None
    for ch in s:
        if q:
            buf.append(ch)
            if ch == q: q = None
            continue
        if ch in '"\'': q = ch; buf.append(ch); continue
        if ch in '[{': depth += 1
        elif ch in ']}': depth -= 1
        if ch == ',' and depth == 0:
            out.append(''.join(buf)); buf = []
        else:
            buf.append(ch)
    if ''.join(buf).strip(): out.append(''.join(buf))
    return out

def _split_kv(s):
    depth, q = 0, None
    for i, ch in enumerate(s):
        if q:
            if ch == q: q = None
            continue
        if ch in '"\'': q = ch; continue
        if ch in '[{': depth += 1
        elif ch in ']}': depth -= 1
        elif ch == ':' and depth == 0:
            return s[:i], s[i + 1:]
    return None, None

def _scalar(s):
    s = s.strip()
    if not s: return None
    if len(s) >= 2 and s[0] in '"\'' and s[-1] == s[0]: return s[1:-1]
    if s.startswith('{') and s.endswith('}'):
        m = {}
        for part in _split_top(s[1:-1]):
            k, v = _split_kv(part)
            if k is not None: m[k.strip().strip('"\'')] = _scalar(v)
        return m
    if s.startswith('[') and s.endswith(']'):
        return [_scalar(p) for p in _split_top(s[1:-1])]
    low = s.lower()
    if low in ('true', 'yes'): return True
    if low in ('false', 'no'): return False
    if low in ('null', '~'): return None
    try: return int(s)
    except ValueError: pass
    try: return float(s)
    except ValueError: pass
    return s

def _strip_comment(line):
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q: q = None
            continue
        if ch in '"\'': q = ch; out.append(ch); continue
        if ch == '#' and (not out or out[-1] in ' \t'): break
        out.append(ch)
    return ''.join(out).rstrip()

def _lines(raw):
    res = []
    for line in raw.splitlines():
        text = _strip_comment(line)
        if not text.strip(): continue
        res.append((len(text) - len(text.lstrip()), text.strip()))
    return res

def _block(lines, idx, indent):
    if lines[idx][1].startswith('-'):
        seq = []
        while idx < len(lines):
            ind, text = lines[idx]
            if ind != indent or not text.startswith('-'): break
            rest = text[1:].strip()
            if not rest:
                idx += 1
                if idx < len(lines) and lines[idx][0] > indent:
                    val, idx = _block(lines, idx, lines[idx][0])
                else:
                    val = None
                seq.append(val)
            elif _split_kv(rest)[0] is not None and not rest.startswith(('{', '[')):
                sub = [(0, rest)]
                j = idx + 1
                while j < len(lines) and lines[j][0] > indent:
                    sub.append((lines[j][0], lines[j][1])); j += 1
                base = min(x[0] for x in sub[1:]) if len(sub) > 1 else 0
                sub = [sub[0]] + [(0 if x[0] == base else x[0], x[1]) for x in sub[1:]]
                val, _ = _block(sub, 0, 0)
                seq.append(val); idx = j
            else:
                seq.append(_scalar(rest)); idx += 1
        return seq, idx
    m = {}
    while idx < len(lines):
        ind, text = lines[idx]
        if ind != indent or text.startswith('-'): break
        k, v = _split_kv(text)
        if k is None: idx += 1; continue
        k = k.strip().strip('"\'')
        if v.strip():
            m[k] = _scalar(v); idx += 1
        else:
            idx += 1
            if idx < len(lines) and lines[idx][0] > ind:
                m[k], idx = _block(lines, idx, lines[idx][0])
            elif idx < len(lines) and lines[idx][0] == ind and lines[idx][1].startswith('-'):
                m[k], idx = _block(lines, idx, ind)
            else:
                m[k] = None
    return m, idx

def load(raw):
    try:
        import yaml
        return yaml.safe_load(raw)
    except ImportError:
        pass
    ls = _lines(raw)
    return _block(ls, 0, ls[0][0])[0] if ls else {}

def get(data, query):
    vals = [data]
    for tok in query.strip('.').split('.'):
        star = tok.endswith('[]')
        if star: tok = tok[:-2]
        nxt = []
        for v in vals:
            if tok:
                if isinstance(v, dict) and tok in v: v = v[tok]
                else: continue
            if star:
                if isinstance(v, list): nxt.extend(v)
                elif v is not None: nxt.append(v)
            else:
                nxt.append(v)
        vals = nxt
    return vals

def render(v):
    if v is None: return ''
    if v is True: return 'true'
    if v is False: return 'false'
    if isinstance(v, (dict, list)): return json.dumps(v, ensure_ascii=False)
    return str(v)

data = load(open(sys.argv[1], encoding='utf-8').read()) or {}
query, fields = sys.argv[2], sys.argv[3:]
for v in get(data, query):
    if fields:
        row = [render(v.get(f)) if isinstance(v, dict) else '' for f in fields]
        # 1 instance = 1 dòng TSV: tab/newline trong giá trị sẽ phá cấu trúc → thay bằng space
        print('\t'.join(x.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ') for x in row))
    else:
        print(render(v))
PYEOF

cfg()  { python3 "$WORK/cfg.py" "$CONFIG" "$@" 2>/dev/null; }
cfgd() { local v; v="$(cfg "$1")"; if [ -n "$v" ]; then printf '%s' "$v"; else printf '%s' "$2"; fi; }

python3 "$WORK/cfg.py" "$CONFIG" project >/dev/null 2>&1 || die "config.yml không parse được: $CONFIG" 1

PROJECT="$(cfgd project "unnamed")"
T_TYPE="$(cfgd transport.type "none")"
R_HOST="$(cfgd transport.host "127.0.0.1")"
R_PORT="$(cfgd transport.port "6379")"
R_DB="$(cfgd transport.db "0")"
R_PASS_ENV="$(cfgd transport.auth_env "")"
WEBHOOK_ENV="$(cfgd delivery.webhook_env "REPORT_WEBHOOK_URL")"
FILE_DIR="$(cfgd delivery.file_dir "docs/system-report")"
KEEP_DAYS="$(cfgd delivery.keep_days "0")"
KNOWN_FILE="$(cfgd known_issues_file "docs/KNOWN_ISSUES.md")"
WATCH_FILE="$(cfgd watchlist_file "ops/system-report/WATCHLIST.md")"
PROMPT_FILE="$(cfgd triage_prompt_file "ops/system-report/triage-prompt.md")"
MAX_LINES="$(cfgd max_log_lines "2000")"
MAX_PER="$(cfgd max_lines_per_instance "400")"
TIMEOUT_SEC="$(cfgd timeout_sec "300")"
CLAUDE_BIN="$(cfgd claude_bin "claude")"
GIT_PULL="$(cfgd git_pull "false")"
DISCOVER="$(cfgd discover_dynamic "true")"

# Repo root = nơi correlate code; mọi path tương đối tính từ đây.
REPO_ROOT="$(cd "$SELF_DIR" && git rev-parse --show-toplevel 2>/dev/null)" || REPO_ROOT=""
[ -n "$REPO_ROOT" ] || REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
cd "$REPO_ROOT" || die "không cd được vào repo root: $REPO_ROOT" 1

abspath() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$REPO_ROOT" "$1" ;; esac; }
KNOWN_FILE="$(abspath "$KNOWN_FILE")"
WATCH_FILE="$(abspath "$WATCH_FILE")"
PROMPT_FILE="$(abspath "$PROMPT_FILE")"
FILE_DIR_ABS="$(abspath "$FILE_DIR")"
LAST_RUN="$SELF_DIR/.last-run"

R_PASS=""
[ -n "$R_PASS_ENV" ] && R_PASS="$(printenv "$R_PASS_ENV" 2>/dev/null || true)"
WEBHOOK_URL="$(printenv "$WEBHOOK_ENV" 2>/dev/null || true)"

# ── Redis adapter ─────────────────────────────────────────────────────────────
rcli() {
  if [ -n "$R_PASS" ]; then
    redis-cli -h "$R_HOST" -p "$R_PORT" -n "$R_DB" -a "$R_PASS" --no-auth-warning "$@"
  else
    redis-cli -h "$R_HOST" -p "$R_PORT" -n "$R_DB" "$@"
  fi
}
redis_ok() { [ "$T_TYPE" = "redis" ] && command -v redis-cli >/dev/null 2>&1 && [ "$(rcli PING 2>/dev/null)" = "PONG" ]; }

# ── --check ───────────────────────────────────────────────────────────────────
check_prereq() {
  local rc=0
  printf 'config      : %s\n' "$CONFIG"
  printf 'project     : %s\n' "$PROJECT"
  printf 'repo root   : %s\n' "$REPO_ROOT"
  for tool in python3 curl; do
    if command -v "$tool" >/dev/null 2>&1; then printf '%-12s: ✅ %s\n' "$tool" "$(command -v $tool)"
    else printf '%-12s: ❌ thiếu\n' "$tool"; rc=1; fi
  done
  if command -v "$CLAUDE_BIN" >/dev/null 2>&1; then printf '%-12s: ✅ %s\n' "claude" "$(command -v "$CLAUDE_BIN")"
  else printf '%-12s: ❌ thiếu (claude_bin=%s) — cron thường không có PATH của shell\n' "claude" "$CLAUDE_BIN"; rc=1; fi
  if command -v timeout >/dev/null 2>&1; then printf '%-12s: ✅\n' "timeout"
  else printf '%-12s: ⚠️  thiếu — triage sẽ chạy KHÔNG timeout\n' "timeout"; fi
  if [ "$T_TYPE" = "redis" ]; then
    if redis_ok; then printf '%-12s: ✅ %s:%s/%s\n' "transport" "$R_HOST" "$R_PORT" "$R_DB"
    else printf '%-12s: ❌ không PING được %s:%s (auth_env=%s %s)\n' "transport" "$R_HOST" "$R_PORT" "$R_PASS_ENV" \
         "$([ -n "$R_PASS" ] && echo SET || echo UNSET)"; rc=1; fi
  else
    printf '%-12s: (none) — chỉ dùng adapter file:/http:/cmd:\n' "transport"
  fi
  for f in "$PROMPT_FILE" "$KNOWN_FILE" "$WATCH_FILE"; do
    [ -f "$f" ] && printf 'file        : ✅ %s\n' "$f" || { printf 'file        : ❌ thiếu %s\n' "$f"; rc=1; }
  done
  mkdir -p "$FILE_DIR_ABS" 2>/dev/null
  [ -w "$FILE_DIR_ABS" ] && printf 'file_dir    : ✅ %s\n' "$FILE_DIR_ABS" || { printf 'file_dir    : ❌ không ghi được %s\n' "$FILE_DIR_ABS"; rc=1; }
  printf 'webhook     : %s (%s)\n' "$WEBHOOK_ENV" "$([ -n "$WEBHOOK_URL" ] && echo SET || echo UNSET)"
  printf 'instances   : %s khai báo · discover_dynamic=%s\n' "$(cfg 'instances[].name' | grep -c . || true)" "$DISCOVER"
  return $rc
}

if [ "$MODE" = "check" ]; then check_prereq; exit $?; fi

# ── Roster: đã đăng ký vs có report hôm nay ───────────────────────────────────
: >"$WORK/registered.txt"; : >"$WORK/reported.txt"; mkdir -p "$WORK/logs"
cfg 'instances[]' name type source optional >"$WORK/instances.tsv" 2>/dev/null || : >"$WORK/instances.tsv"

TRANSPORT_ERR=0
if [ "$T_TYPE" = "redis" ]; then
  if redis_ok; then
    rcli --raw SMEMBERS REPORT_INSTANCES 2>/dev/null | grep -v '^$' | sort -u >"$WORK/registered.txt"
  else
    log "WARN: transport redis không truy cập được ($R_HOST:$R_PORT)"; TRANSPORT_ERR=1
  fi
fi

# ── --status ──────────────────────────────────────────────────────────────────
if [ "$MODE" = "status" ]; then
  echo "📋 system-report · $PROJECT · $DATE"
  echo "--- instances ---"
  if [ "$T_TYPE" = "redis" ] && [ "$TRANSPORT_ERR" = "0" ]; then
    rcli --scan --pattern "REPORT_DAILY:${DATE}:*" 2>/dev/null \
      | sed "s/^REPORT_DAILY:${DATE}://" | sort -u >"$WORK/reported.txt"
  fi
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    if grep -qxF "$n" "$WORK/reported.txt" 2>/dev/null; then echo "  ✅ $n — có report hôm nay"
    else echo "  ⚠️  $n — ĐÃ ĐĂNG KÝ nhưng VẮNG report (nghi chết)"; fi
  done <"$WORK/registered.txt"
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    grep -qxF "$n" "$WORK/registered.txt" 2>/dev/null || echo "  🆕 $n — report hôm nay, chưa có trong REPORT_INSTANCES"
  done <"$WORK/reported.txt"
  [ -s "$WORK/registered.txt" ] || echo "  (chưa instance nào đăng ký — Reporter đã bật REPORT_ENABLED=true chưa?)"
  echo "--- cron ---"
  echo "  config : $(cfgd cron '(chưa đặt)')"
  echo "  crontab: $(crontab -l 2>/dev/null | grep -F 'system-report' || echo '(không thấy dòng nào)')"
  echo "--- delivery ---"
  echo "  webhook: $WEBHOOK_ENV = $([ -n "$WEBHOOK_URL" ] && echo SET || echo UNSET)"
  echo "  file_dir: $FILE_DIR_ABS ($(ls -1 "$FILE_DIR_ABS" 2>/dev/null | wc -l | tr -d ' ') file)"
  echo "--- watchlist đang mở ---"
  grep -nE '^##+ *WATCH-' "$WATCH_FILE" 2>/dev/null | sed 's/^/  /' || echo "  (rỗng)"
  echo "--- lần chạy gần nhất ---"
  [ -f "$LAST_RUN" ] && sed 's/^/  /' "$LAST_RUN" || echo "  (chưa chạy lần nào)"
  exit 0
fi

# ── git pull (chỉ khi tree sạch — không bao giờ phá working tree) ─────────────
if [ "$GIT_PULL" = "true" ] && git rev-parse --git-dir >/dev/null 2>&1; then
  if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
    git pull --ff-only >>"$WORK/git.log" 2>&1 && log "git pull ok" || log "WARN: git pull fail (xem log)"
  else
    log "WARN: working tree bẩn → bỏ qua git pull"
  fi
fi

# ── Gom log ───────────────────────────────────────────────────────────────────
# Lọc: giữ nguyên nếu nhỏ; lớn thì ưu tiên ERROR/WARN + ngữ cảnh, cắt trần.
SIGNAL_RE='ERROR|WARN|FATAL|CRITICAL|SEVERE|Exception|Traceback|panic:|OOM|timeout|refused|failed'
filter_log() { # stdin → stdout ; $1 = nhãn instance
  local raw="$WORK/raw.$$" total kept
  cat >"$raw"
  total=$(wc -l <"$raw" | tr -d ' ')
  if [ "$total" -le "$MAX_PER" ]; then
    cat "$raw"; printf '\n(%s: %s dòng, giữ nguyên)\n' "$1" "$total"
  else
    if grep -qiE "$SIGNAL_RE" "$raw"; then
      grep -iE -B1 -A2 "$SIGNAL_RE" "$raw" | head -n "$MAX_PER" >"$raw.f"
    else
      tail -n "$MAX_PER" "$raw" >"$raw.f"
    fi
    kept=$(wc -l <"$raw.f" | tr -d ' ')
    cat "$raw.f"
    printf '\n(%s: đã lọc %s/%s dòng — ưu tiên ERROR/WARN + ngữ cảnh; phần còn lại bị cắt)\n' "$1" "$kept" "$total"
    rm -f "$raw.f"
  fi
  rm -f "$raw"
}

fetch_source() { # $1 name, $2 source  → stdout raw log
  local name="$1" src="$2" path
  case "$src" in
    redis:*|"")
      redis_ok || return 1
      rcli --raw GET "REPORT_DAILY:${DATE}:${name}" 2>/dev/null ;;
    file:*)
      path="${src#file:}"; path="${path//\{date\}/$DATE}"
      [ -r "$path" ] || return 1
      cat "$path" ;;
    cmd:*)
      local c="${src#cmd:}"; c="${c//\{date\}/$DATE}"
      bash -c "$c" 2>&1 ;;
    http:*|https:*)
      local u="$src"
      case "$u" in http:http*) u="${u#http:}" ;; esac   # dạng "http:https://..."
      u="${u//\{date\}/$DATE}"
      curl -fsS --max-time 30 ${REPORT_HTTP_AUTH:+-H "Authorization: $REPORT_HTTP_AUTH"} "$u" 2>/dev/null ;;
    *) return 1 ;;
  esac
}

# 1) instance khai báo trong config
while IFS=$'\t' read -r NAME TYPE SRC OPT; do
  [ -n "${NAME:-}" ] || continue
  if out="$(fetch_source "$NAME" "${SRC:-redis:REPORT_DAILY}")" && [ -n "$out" ]; then
    printf '%s' "$out" | filter_log "$NAME" >"$WORK/logs/$NAME.log"
    echo "$NAME" >>"$WORK/reported.txt"
  fi
done <"$WORK/instances.tsv"

# 2) khám phá instance động trên transport (topology thay đổi mà không sửa config)
if [ "$DISCOVER" != "false" ] && [ "$T_TYPE" = "redis" ] && [ "$TRANSPORT_ERR" = "0" ]; then
  rcli --scan --pattern "REPORT_DAILY:${DATE}:*" 2>/dev/null | sed "s/^REPORT_DAILY:${DATE}://" | sort -u \
  | while IFS= read -r n; do
      [ -n "$n" ] || continue
      [ -f "$WORK/logs/$n.log" ] && continue
      rcli --raw GET "REPORT_DAILY:${DATE}:${n}" 2>/dev/null | filter_log "$n" >"$WORK/logs/$n.log"
      echo "$n" >>"$WORK/reported.txt"
    done
fi
sort -u -o "$WORK/reported.txt" "$WORK/reported.txt" 2>/dev/null || true

MISSING=""
while IFS= read -r n; do
  [ -n "$n" ] || continue
  grep -qxF "$n" "$WORK/reported.txt" 2>/dev/null && continue
  # instance đánh dấu optional thì vắng là bình thường
  awk -F'\t' -v n="$n" '$1==n && $4=="true"{f=1} END{exit !f}' "$WORK/instances.tsv" 2>/dev/null && continue
  MISSING="$MISSING $n"
done <"$WORK/registered.txt"

REPORTED_N=$(grep -c . "$WORK/reported.txt" 2>/dev/null || echo 0)
log "gom log: $REPORTED_N instance có dữ liệu; vắng:${MISSING:- (không)}"

# ── Build payload cho AI (đi qua STDIN → tránh ARG_MAX) ───────────────────────
PAYLOAD="$WORK/payload.md"
{
  echo "# DỮ LIỆU BÁO CÁO — dự án: $PROJECT — ngày: $DATE"
  echo
  echo "## 1. Roster instance"
  echo "- Đã đăng ký (REPORT_INSTANCES): $(tr '\n' ' ' <"$WORK/registered.txt")"
  echo "- Có report hôm nay: $(tr '\n' ' ' <"$WORK/reported.txt")"
  echo "- VẮNG report dù đã đăng ký (NGHI CHẾT):${MISSING:- (không có)}"
  [ "$TRANSPORT_ERR" = "1" ] && echo "- ⚠️ TRANSPORT KHÔNG TRUY CẬP ĐƯỢC ($R_HOST:$R_PORT) — dữ liệu có thể thiếu."
  echo
  echo "### Bảng khai báo (name / type / source / optional)"
  echo '```'
  cat "$WORK/instances.tsv"
  echo '```'
  echo
  echo "## 2. Tín hiệu domain cần soi (theo config dự án)"
  cfg 'domain_signals[]' | sed 's/^/- /'
  echo
  echo "## 3. Subtree code để correlate (file:line)"
  cfg 'correlate_paths[]' | sed 's/^/- /'
  echo
  echo "## 4. KNOWN_ISSUES.md (lỗi ĐÃ ĐÓNG — kiểm regression)"
  echo '```markdown'
  [ -f "$KNOWN_FILE" ] && cat "$KNOWN_FILE" || echo "(chưa có file $KNOWN_FILE)"
  echo '```'
  echo
  echo "## 5. WATCHLIST.md (đang theo dõi — bạn phải xuất lại bản cập nhật)"
  echo '```markdown'
  [ -f "$WATCH_FILE" ] && cat "$WATCH_FILE" || echo "(chưa có file $WATCH_FILE)"
  echo '```'
  echo
  echo "## 6. LOG THEO INSTANCE"
  if [ -z "$(ls -A "$WORK/logs" 2>/dev/null)" ]; then
    echo "(KHÔNG instance nào có log hôm nay — đây tự nó đã là bất thường)"
  else
    for f in "$WORK/logs"/*.log; do
      n="$(basename "$f" .log)"
      t="$(awk -F'\t' -v n="$n" '$1==n{print $2}' "$WORK/instances.tsv" 2>/dev/null | head -1)"
      echo
      echo "### instance: $n (type: ${t:-unknown})"
      echo '```log'
      cat "$f"
      echo '```'
    done
  fi
} >"$PAYLOAD"

# cắt trần tổng thể
if [ "$(wc -l <"$PAYLOAD")" -gt "$MAX_LINES" ]; then
  head -n "$MAX_LINES" "$PAYLOAD" >"$PAYLOAD.cut"
  echo -e "\n\n(⚠️ payload đã bị cắt ở $MAX_LINES dòng — max_log_lines)" >>"$PAYLOAD.cut"
  mv "$PAYLOAD.cut" "$PAYLOAD"
fi

if [ "$MODE" = "dry" ]; then
  log "--dry-run: payload tại $PAYLOAD ($(wc -l <"$PAYLOAD") dòng) — copy ra trước khi thoát"
  cp "$PAYLOAD" "$SELF_DIR/.last-payload.md" && log "đã lưu $SELF_DIR/.last-payload.md"
  head -n 60 "$PAYLOAD"
  exit 0
fi

# ── AI triage READ-ONLY ───────────────────────────────────────────────────────
[ -f "$PROMPT_FILE" ] || die "không thấy triage prompt: $PROMPT_FILE" 1
OUT="$WORK/out.md"; ERR="$WORK/err.log"
TIMEOUT_CMD=""
command -v timeout >/dev/null 2>&1 && TIMEOUT_CMD="timeout ${TIMEOUT_SEC}s"

PROMPT_TEXT="$(cat "$PROMPT_FILE")
--- Ngày báo cáo: $DATE · Dự án: $PROJECT ---
Dữ liệu (roster + known issues + watchlist + log) nằm ở STDIN."

log "triage: gọi $CLAUDE_BIN (timeout ${TIMEOUT_SEC}s, read-only)"
# shellcheck disable=SC2086
$TIMEOUT_CMD "$CLAUDE_BIN" -p "$PROMPT_TEXT" \
  --output-format text \
  --allowedTools "Read,Grep,Glob" \
  ${CLAUDE_EXTRA_ARGS:-} \
  <"$PAYLOAD" >"$OUT" 2>"$ERR"
TRIAGE_RC=$?

extract() { awk -v m="===$1===" 'index($0,m)==1{f=1;next} /^===[A-Z]+===$/{f=0} f{print}' "$2"; }

DIGEST=""; REPORT=""; NEWWATCH=""
if [ "$TRIAGE_RC" -eq 0 ] && [ -s "$OUT" ]; then
  DIGEST="$(extract DIGEST "$OUT")"
  REPORT="$(extract REPORT "$OUT")"
  NEWWATCH="$(extract WATCHLIST "$OUT")"
fi

if [ -z "$DIGEST" ]; then
  # Heartbeat guardrail: im lặng KHÔNG BAO GIỜ được hiểu là "mọi thứ tốt".
  REASON="triage lỗi (rc=$TRIAGE_RC)"
  [ "$TRIAGE_RC" -eq 124 ] && REASON="triage TIMEOUT sau ${TIMEOUT_SEC}s"
  [ "$TRIAGE_RC" -eq 0 ] && [ -s "$OUT" ] && REASON="triage trả output sai contract (thiếu ===DIGEST===)"
  DIGEST="📋 $PROJECT · $DATE · 🔴 CẦN XỬ LÝ
[🔴CAO] TRIAGE FAILED — không sinh được báo cáo: $REASON
Instance có log: ${REPORTED_N} · vắng:${MISSING:- (không)}
→ Nghiêm trọng nhất: hệ thống báo cáo tự nó đang hỏng, kiểm cron/claude CLI/transport."
  REPORT="# $PROJECT — $DATE — TRIAGE FAILED

$REASON (exit $TRIAGE_RC).

## stderr
\`\`\`
$(tail -n 40 "$ERR" 2>/dev/null)
\`\`\`

## output thô
\`\`\`
$(head -n 80 "$OUT" 2>/dev/null)
\`\`\`
"
  [ "$TRIAGE_RC" -eq 0 ] && TRIAGE_RC=3
fi

# ── Ghi file (chỉ trong thư mục của chính nó) ────────────────────────────────
mkdir -p "$FILE_DIR_ABS"
REPORT_PATH="$FILE_DIR_ABS/$DATE.md"
printf '%s\n' "$REPORT" >"$REPORT_PATH"
log "đã ghi $REPORT_PATH"

if [ -n "$NEWWATCH" ]; then
  mkdir -p "$(dirname "$WATCH_FILE")"
  printf '%s\n' "$NEWWATCH" >"$WATCH_FILE"
  log "đã cập nhật $WATCH_FILE"
else
  log "không có block ===WATCHLIST=== → giữ nguyên file cũ (fail-safe)"
fi

if [ "$KEEP_DAYS" != "0" ] && [ -n "$KEEP_DAYS" ]; then
  find "$FILE_DIR_ABS" -name '*.md' -type f -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
fi

# ── Giao webhook ──────────────────────────────────────────────────────────────
WEBHOOK_RC=0
if [ "$SEND_WEBHOOK" = "1" ] && [ -n "$WEBHOOK_URL" ]; then
  BODY="$WORK/body.json"
  DIGEST="$DIGEST" PROJECT="$PROJECT" DATE="$DATE" URL="$WEBHOOK_URL" python3 - >"$BODY" <<'PYEOF'
import json, os
d, url = os.environ['DIGEST'], os.environ['URL']
if 'discord.com/api/webhooks' in url or 'discordapp.com/api/webhooks' in url:
    payload = {"content": d[:1900]}
elif 'hooks.slack.com' in url:
    payload = {"text": d[:3800]}
else:
    payload = {"project": os.environ['PROJECT'], "date": os.environ['DATE'], "digest": d}
print(json.dumps(payload, ensure_ascii=False))
PYEOF
  if curl -fsS --max-time 30 -H 'Content-Type: application/json' -X POST -d @"$BODY" "$WEBHOOK_URL" >/dev/null 2>&1; then
    log "đã gửi digest tới webhook ($WEBHOOK_ENV)"
  else
    log "ERROR: gửi webhook thất bại — bản đầy đủ vẫn ở $REPORT_PATH"; WEBHOOK_RC=4
  fi
elif [ "$SEND_WEBHOOK" = "1" ]; then
  log "WARN: $WEBHOOK_ENV chưa set → bỏ qua webhook (chỉ ghi file)"
fi

echo "----- DIGEST -----"; printf '%s\n' "$DIGEST"; echo "------------------"

RC=0
[ "$TRANSPORT_ERR" = "1" ] && RC=2
[ "$TRIAGE_RC" -ne 0 ] && RC=3
[ "$WEBHOOK_RC" -ne 0 ] && RC=4
{
  echo "date=$DATE"
  echo "finished=$(date -Iseconds)"
  echo "exit=$RC"
  echo "instances_reported=$REPORTED_N"
  echo "missing=${MISSING:-none}"
  echo "report=$REPORT_PATH"
} >"$LAST_RUN"
exit $RC

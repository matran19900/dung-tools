---
name: system-report
description: >
  Báo cáo sức khoẻ hệ thống TỰ ĐỘNG hằng ngày cho MỌI dự án. Gom log đa-instance
  → 1 phiên AI triage READ-ONLY (đọc log + đối chiếu code + known-issues) → digest
  xếp hạng NẶNG→NHẸ gửi tới chủ dự án (webhook + file). Reusable: `/system-report:init`
  scaffold vào bất kỳ repo nào; runner chạy bằng cron, không cần người trực.
---

# system-report — báo cáo sức khoẻ hệ thống tự động (reusable)

> Skill này là **cơ chế thuần, không gắn dự án nào**. Nó (1) mô tả framework portable,
> (2) `init` scaffold cấu hình + tooling vào repo hiện tại, (3) `run` sinh báo cáo mỗi ngày.
> Đặc thù dự án (component nào, log ở đâu, tín hiệu domain) sống trong **config của dự án**,
> KHÔNG trong skill — giống cách `roles:cto` tách mechanism khỏi PROJECT_CONTEXT.

## 1. Bài toán skill giải
Chủ dự án muốn **mỗi ngày tự nhận 1 báo cáo** "hệ thống ổn / có vấn đề gì, xếp nặng→nhẹ",
**không phải hỏi, không vào máy chạy tay**. Chỉ khi có vấn đề nghiêm trọng mới bắt tay xử lý.
Báo cáo là **1 chiều**; vòng fix để con người chủ động (skill KHÔNG tự sửa hệ thống).

## 2. Khi nào dùng
- User gọi `/system-report:init` để trang bị cho 1 dự án.
- Cron/`/system-report:run` chạy hằng ngày để sinh báo cáo.
- User gọi `/system-report:status` để xem cấu hình + độ sẵn sàng.

## 3. Framework portable (không đổi giữa các dự án)

### 3.1 Kiến trúc
```
[instance 1] Reporter ┐  (opt-in, mặc định TẮT)
[instance 2] Reporter ┼─push─▶ Transport (mặc định Redis-log RIÊNG)
[instance N] Reporter ┘            │  REPORT_DAILY:{yyyy-MM-dd}:{INSTANCE}
                                   │  (SCAN để khám phá instance động)
[triage box 24/7]  cron ─mỗi ngày─▶ runner (run.sh)
                                   ├─ AI triage READ-ONLY (claude -p trong repo checkout)
                                   │    · đối chiếu KNOWN_ISSUES (regression?)
                                   │    · đối chiếu WATCHLIST (đang theo dõi)
                                   │    · soi anomaly mới, correlate code file:line
                                   └─ GIAO: webhook digest + file đầy đủ (dated)
```

### 3.2 Contract dữ liệu (generic)
Transport mặc định = Redis instance **RIÊNG** (tách hạ tầng production của dự án). 3 key:
| Key | Ai ghi | Mục đích |
|---|---|---|
| `APPEND/SET REPORT_DAILY:{yyyy-MM-dd}:{INSTANCE}` | Reporter | log/telemetry ngày (có TTL) |
| `SADD REPORT_INSTANCES {INSTANCE}` (1 lần/start) | Reporter | đăng ký "tôi tồn tại" → bắt instance chết |
| `XADD REPORT_EVENT:{INSTANCE} ...` *(mở rộng)* | Reporter | event real-time (không chỉ daily) |
- `INSTANCE` = tên bản chạy, nhiều bản/loại: `web_1`, `worker_A`, `db_2`… topology **động**.
- Đặt tên `{TYPE}_...` (prefix theo loại) để triage suy ra loại + gộp; hoặc Reporter đẩy kèm field `type`.
- **Transport có thể thay** (Redis / file / HTTP / cmd) — runner đọc qua 1 adapter; Redis là mặc định vì rẻ + đa-máy.

### 3.3 Trí nhớ 2 lớp (thay session state — mỗi run là phiên mới)
- **`KNOWN_ISSUES.md`** (aka fix diary) — lỗi **đã đóng** → checklist regression ("có tái phát không?").
- **`WATCHLIST.md`** — lỗi **đang mở/theo dõi**. Chủ dự án thêm 1 khối `WATCH-*` → mỗi ngày triage
  đọc, cập nhật tần suất, escalate khi vượt ngưỡng, đề xuất đóng khi vắng N ngày.

### 3.4 Bất biến / guardrail (BẮT BUỘC mọi dự án)
1. **Triage READ-ONLY hệ thống:** chỉ chẩn đoán/đề xuất; KHÔNG sửa/commit/deploy code sản phẩm.
   Phiên AI thậm chí **không tự ghi file** — nó xuất text theo contract §5.1, **runner** ghi report + WATCHLIST.
2. **Reporter airtight:** nuốt mọi exception, async — telemetry hỏng KHÔNG ảnh hưởng hệ thống.
3. **Reporter opt-in:** mặc định TẮT (`REPORT_ENABLED=false`); build có nhưng trơ tới khi bật.
4. **Reporter tách file, gỡ 1 nốt:** toàn bộ trong 1 file riêng + wiring đúng 1 dòng → xoá file + 1 dòng là hết.
5. **Transport tách hạ tầng production** (Redis-log riêng, có auth, chỉ namespace `REPORT_*`).
6. **Heartbeat:** LUÔN gửi báo cáo kể cả ngày ổn → im lặng = cron/triage hỏng, không phải "mọi thứ tốt".
   Triage fail/timeout → runner vẫn gửi digest 🔴 "TRIAGE FAILED".
7. **Bắt instance chết:** diff SCAN kết quả vs `REPORT_INSTANCES` → đã đăng ký mà vắng report → cảnh báo.
8. **Timeout** mỗi run (AI có thể treo). **Secrets** (webhook, transport auth) chỉ qua **env**, không hard-code/commit.

## 4. Workflows (nội dung 3 command)

### `/system-report:init` — trang bị vào dự án hiện tại
Mục tiêu: sinh **config + tooling đã adapt** cho repo này. Các bước Claude làm:
1. **Khám phá dự án:** đọc cấu trúc repo, tìm các component/service + nguồn log (file path / lệnh / key).
   Hỏi user xác nhận danh sách **instance** + **nguồn log** từng cái.
2. Hỏi/chốt: **transport** (Redis endpoint riêng?), **delivery** (webhook URL? thư mục file report?),
   **giờ cron**, **subtree code để correlate**, **tín hiệu domain** cần chú ý (vd với web: 5xx, latency,
   queue backlog; với data-pipeline: job fail, trễ; …) — đây là phần **project-specific**.
3. **Sinh file vào dự án** (copy từ `templates/`, đã điền config):
   - `<project>/ops/system-report/config.yml`
   - `<project>/ops/system-report/run.sh` (copy nguyên bản, chmod +x — runner generic, đọc config.yml)
   - `<project>/ops/system-report/triage-prompt.md` (đã chèn domain signals + trỏ KNOWN_ISSUES/WATCHLIST)
   - `<project>/docs/KNOWN_ISSUES.md` + `<project>/ops/system-report/WATCHLIST.md` (scaffold rỗng)
   - Reporter skeleton phù hợp ngôn ngữ mỗi component (in ra hướng dẫn gắn 1 dòng + để user tự thêm —
     **KHÔNG tự sửa code sản phẩm**).
4. **In ra:** dòng cron mẫu + cách bật Reporter (`REPORT_ENABLED=true`) + checklist verify.
5. KHÔNG tự commit — để user review diff rồi commit.

### `/system-report:run` — sinh báo cáo hôm nay (cron gọi cái này)
Đầu vào: `config.yml`. Các bước (đã hiện thực trong `templates/run.sh`):
1. `git pull` (nếu cấu hình) để correlate đúng code đang deploy.
2. **Gom log**: `SCAN REPORT_DAILY:{today}:*` để khám phá instance động; đọc thêm instance khai báo
   qua adapter `file:` / `http:` / `cmd:`. Log lớn → lọc ưu tiên ERROR/WARN + ngữ cảnh, cắt trần dòng.
3. **AI triage READ-ONLY** (`claude -p`, chạy trong repo checkout, có `timeout`, log qua **stdin**):
   - Diff instance đã report vs `REPORT_INSTANCES` → instance vắng = "nghi chết".
   - Đối chiếu `KNOWN_ISSUES.md` (regression) + `WATCHLIST.md` (đang theo dõi).
   - Soi anomaly mới theo **domain signals** của dự án; correlate code (`file:line`).
   - Xuất theo contract §5.1.
4. **Runner** tách output → ghi `WATCHLIST.md` (tần suất/escalate/đóng) + file report dated.
5. **Giao**: digest gọn → webhook; bản đầy đủ → `{file_dir}/{date}.md`. Heartbeat: luôn gửi.
6. Exit code rõ cho cron (§5.2); không throw ra ngoài làm hỏng lịch.

### `/system-report:status`
In: instances đã đăng ký vs report hôm nay (ai thiếu), giờ cron, delivery target, WATCHLIST đang mở, lần chạy gần nhất.

## 5. Format báo cáo chuẩn (digest — đọc mobile)
```
📋 {PROJECT} · {date} · 🟢 ỔN ĐỊNH | 🟡 CẢNH BÁO | 🔴 CẦN XỬ LÝ
{rollup instance gộp theo loại: web_1 ✅ · worker_A 🔴 · db_1 ⚠️}
{mỗi finding: [🔴CAO/🟡VỪA/⚪THẤP] mô tả 1 dòng · bằng chứng (trích log) · file:line · đề xuất}
Đang theo dõi: {WATCH-xxx: trạng thái hôm nay}
→ Nghiêm trọng nhất: {1 dòng} (nếu có)
```
Ngày ổn vẫn gửi 1 dòng (heartbeat). Bản đầy đủ (bằng chứng + file:line) trong file dated.

### 5.1 Contract output của phiên triage (AI → runner)
Phiên AI **chỉ in text**; runner parse 3 block (marker phải ở đầu dòng, không thừa ký tự):
```
===DIGEST===      → digest §5, ≤ ~15 dòng, gửi webhook
===REPORT===      → bản đầy đủ markdown, ghi {file_dir}/{date}.md
===WATCHLIST===   → TOÀN BỘ nội dung mới của WATCHLIST.md (runner ghi đè)
===END===
```
Thiếu `===WATCHLIST===` → runner giữ nguyên file cũ (fail-safe, không bao giờ xoá trắng).

### 5.2 Exit code của runner
`0` ok · `1` lỗi config/prereq · `2` lỗi transport (không gom được log) · `3` triage fail/timeout
(vẫn gửi digest báo động) · `4` giao webhook thất bại (file vẫn ghi).

## 6. Project-specific config (do init sinh, sống trong dự án)
Khung đầy đủ + chú thích: `templates/config.example.yml`.
```yaml
project: "<tên>"
transport: { type: redis, host, port, db, auth_env: REPORT_REDIS_PASS }
instances:
  - { name: web_1, type: web, source: "redis:REPORT_DAILY" }
  - { name: worker_A, type: worker, source: "file:/var/log/worker/{date}.log" }
delivery:
  webhook_env: REPORT_WEBHOOK_URL      # Discord/Slack/generic
  file_dir: docs/system-report
correlate_paths: [ "src/", "services/" ]
domain_signals: [ "5xx spike", "queue backlog > N", "job failed", "..." ]
cron: "7 8 * * *"
git_pull: true
max_log_lines: 2000
timeout_sec: 300
```

## 7. Reporter module (opt-in, tách file, airtight) — templates cho nhiều ngôn ngữ
Nguyên tắc §3.4 (2)(3)(4). Skeleton phải: đọc config qua **env** (`REPORT_ENABLED`, `REPORT_INSTANCE`,
transport), `SADD REPORT_INSTANCES` khi start, `APPEND REPORT_DAILY:{date}:{INSTANCE}` + TTL,
wiring đúng **1 dòng**, header ghi rõ "optional telemetry — default off — xoá file này + 1 dòng để loại bỏ".
Ship skeleton: **C#, Python, Node/TS, Go** — tất cả **zero-dependency** (nói RESP thẳng qua TCP socket),
fire-and-forget: hàng đợi có trần, đầy thì **drop**, mọi exception bị nuốt, không bao giờ block caller.

## 8. Cấu trúc skill (file tham chiếu)
```
plugins/system-report/
├── .claude-plugin/plugin.json
├── commands/{init,run,status}.md         # 3 workflow §4 (ngắn, ủy quyền về SKILL.md này)
└── skills/system-report/
    ├── SKILL.md                          # file này (entry + framework)
    ├── templates/
    │   ├── config.example.yml            # §6
    │   ├── run.sh                        # runner: config → adapter transport → claude -p (timeout, stdin) → giao
    │   ├── triage-prompt.md              # §5 + §5.1 + guardrail READ-ONLY + chèn domain_signals
    │   ├── KNOWN_ISSUES.md               # scaffold (regression checklist)
    │   ├── WATCHLIST.md                  # scaffold (khối WATCH-* + schema tần suất/ngưỡng)
    │   ├── reporter.{cs,py,ts,go}        # §7 skeleton opt-in, airtight, zero-dep
    │   ├── cron.snippet                  # dòng cron mẫu
    │   └── transport-redis.conf          # Redis-log riêng (isolated, auth, chỉ REPORT_*)
    └── reference/ARCHITECTURE.md         # framework portable (từ §3), cho người đọc
```
Đường dẫn khi chạy: `${CLAUDE_PLUGIN_ROOT}/skills/system-report/templates/<file>`.
Mọi template **generic** (placeholder `{{PROJECT}}`, `{{INSTANCE}}`…), KHÔNG hard-code dự án nào.

## 9. Quan hệ với skill khác
- **Độc lập** với `roles:cto` (không nhúng). `roles:cto` chỉ **cross-ref**: "muốn giám sát sức khoẻ
  hằng ngày → dùng skill `system-report`". Giữ CTO gọn, tránh bloat.
- Có thể ghép sau: CTO thứ 2 verify đối kháng findings trước khi lên chủ dự án (giảm báo động giả).

## 10. Checklist verify sau khi init (chạy được, không chỉ đọc)
- [ ] `bash ops/system-report/run.sh --check` → in prereq (python3, claude, redis-cli/curl) + config hợp lệ.
- [ ] `bash ops/system-report/run.sh --dry-run` → gom log + build payload, KHÔNG gọi AI, KHÔNG gửi webhook.
- [ ] 1 lần chạy thật → digest đúng format §5 + file `{file_dir}/{date}.md` + WATCHLIST được cập nhật.
- [ ] Ngày sạch vẫn có digest (heartbeat); tắt transport → vẫn có digest báo động (exit 2/3).
- [ ] Instance đăng ký mà vắng report → xuất hiện mục "nghi chết".
- [ ] Reporter: `REPORT_ENABLED` chưa bật → hệ thống chạy y hệt; bật lên → key `REPORT_*` xuất hiện.
- [ ] `git grep -nE 'https://(discord|hooks\.slack)' ops/ docs/` rỗng → secrets không lọt vào repo.

---
*Hết. Cơ chế thuần. Đặc thù dự án → `ops/system-report/config.yml`. Triage READ-ONLY: chỉ chẩn đoán, người quyết.*

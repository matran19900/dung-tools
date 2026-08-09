---
description: Trang bị báo cáo sức khoẻ hệ thống tự động cho repo hiện tại (khám phá dự án → hỏi chốt → scaffold config + runner + reporter).
argument-hint: "[ghi chú tuỳ chọn: tên dự án / instance đã biết]"
---

# /system-report:init

Đọc **trước**: `${CLAUDE_PLUGIN_ROOT}/skills/system-report/SKILL.md` — §3 (framework + guardrail),
§4 (`init`), §6 (config), §7 (reporter). File này chỉ là trình tự thực thi; chi tiết ở SKILL.md.

Ghi chú user: $ARGUMENTS

## Trình tự

1. **Khám phá repo (đọc, không sửa)** — liệt kê component/service chạy được, ngôn ngữ mỗi cái,
   nguồn log khả dĩ (file path, `journalctl`, docker logs, key sẵn có). Tóm tắt cho user.
2. **Hỏi chốt** (dùng question-tool cho fact đơn lẻ, thảo luận trong chat cho tradeoff):
   - Danh sách **instance** (`name`, `type`, `source`) — xác nhận/bổ sung.
   - **Transport**: Redis-log riêng (host/port/db) hay adapter khác (`file:` / `http:` / `cmd:`).
   - **Delivery**: tên env chứa webhook URL + thư mục file report.
   - **Giờ cron**, **correlate_paths**, **domain_signals** (tín hiệu domain cần chú ý).
   - ⚠️ **Không nhận giá trị secret trực tiếp** — chỉ nhận **tên env** (§3.4-8).
3. **Sinh file vào dự án** (copy từ `${CLAUDE_PLUGIN_ROOT}/skills/system-report/templates/`,
   thay placeholder `{{...}}`; KHÔNG đè file đã tồn tại — hỏi trước):
   - `ops/system-report/config.yml`  ← `config.example.yml` đã điền
   - `ops/system-report/run.sh`      ← copy nguyên bản + `chmod +x`
   - `ops/system-report/triage-prompt.md` ← đã chèn `domain_signals` + đường dẫn KNOWN_ISSUES/WATCHLIST
   - `ops/system-report/WATCHLIST.md`, `docs/KNOWN_ISSUES.md` ← scaffold rỗng
   - `ops/system-report/reporter.<ext>` ← skeleton đúng ngôn ngữ **từng** component
     (chỉ **copy file**; **KHÔNG** tự sửa code sản phẩm để wiring).
4. **Verify tại chỗ**: chạy `bash ops/system-report/run.sh --check` rồi `--dry-run`; sửa config nếu fail.
5. **In ra cho user** (không commit — §4.5):
   - Dòng wiring **1 dòng** cần thêm vào mỗi component (từ header của reporter skeleton).
   - `cron.snippet` đã điền đường dẫn thật.
   - Env cần set: `REPORT_ENABLED=true`, `REPORT_INSTANCE=<name>`, transport auth, webhook.
   - Checklist verify §10 của SKILL.md.
6. Nhắc user thêm `.gitignore`/env-file nếu cần, và **tự review diff rồi commit**.

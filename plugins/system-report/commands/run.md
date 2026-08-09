---
description: Sinh báo cáo sức khoẻ hôm nay — gom log đa-instance → AI triage READ-ONLY → digest webhook + file dated. (Cron nên gọi run.sh trực tiếp.)
argument-hint: "[--date YYYY-MM-DD] [--config path] [--dry-run]"
---

# /system-report:run

> **Cron KHÔNG cần command này** — cron gọi thẳng `ops/system-report/run.sh` (xem `cron.snippet`).
> Command này để chạy tay / debug / chạy bù ngày cũ.

Đọc `${CLAUDE_PLUGIN_ROOT}/skills/system-report/SKILL.md` §4 (`run`), §5 (format + contract output), §5.2 (exit code).

## Trình tự

1. Tìm `ops/system-report/config.yml` (hoặc `--config`). Không có → bảo user chạy `/system-report:init`, dừng.
2. Chạy runner, để nó tự làm toàn bộ vòng gom-log → triage → giao:
   ```bash
   bash ops/system-report/run.sh $ARGUMENTS
   ```
3. Đọc exit code + log runner, báo lại user gọn:
   - `0` xong (đường dẫn file report + đã gửi webhook chưa)
   - `1` config/prereq sai → chỉ đúng dòng cần sửa
   - `2` transport → không gom được log
   - `3` triage fail/timeout → digest báo động đã gửi
   - `4` webhook fail → file vẫn ghi, đưa đường dẫn
4. **KHÔNG** tự sửa hệ thống/code sản phẩm dựa trên finding (guardrail §3.4-1) — chỉ tóm tắt +
   đề xuất. Muốn fix → user mở phiên `/roles:cto` hoặc `/roles:em`.

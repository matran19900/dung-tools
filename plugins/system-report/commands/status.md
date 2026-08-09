---
description: Xem độ sẵn sàng của system-report trong repo này — instance đăng ký vs report hôm nay, cron, delivery, WATCHLIST đang mở, lần chạy gần nhất.
---

# /system-report:status

Đọc `${CLAUDE_PLUGIN_ROOT}/skills/system-report/SKILL.md` §3.2 (contract) khi cần diễn giải key.
Chỉ **đọc**, không sửa gì.

## Trình tự

1. `ops/system-report/config.yml` — không có → "chưa init", gợi ý `/system-report:init`, dừng.
2. Chạy `bash ops/system-report/run.sh --status` (in sẵn phần lớn thông tin dưới đây) và
   bổ sung bằng cách đọc file khi cần.
3. Trình bày 1 bảng gọn:
   - **Instances**: đã đăng ký (`SMEMBERS REPORT_INSTANCES`) vs đã có report hôm nay
     (`SCAN REPORT_DAILY:{today}:*`) → cột **thiếu** = nghi chết.
   - **Cron**: `cron` trong config vs crontab thực tế (`crontab -l | grep system-report`) → khớp không.
   - **Delivery**: `file_dir` + env webhook đã set chưa (chỉ báo SET/UNSET, **không in giá trị**).
   - **WATCHLIST**: các khối `WATCH-*` đang mở + trạng thái.
   - **Lần chạy gần nhất**: file mới nhất trong `file_dir` + `ops/system-report/.last-run`.
4. Kết luận 1 dòng: sẵn sàng ✅ / thiếu gì ⚠️ (kèm lệnh sửa cụ thể).

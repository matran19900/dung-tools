# WATCHLIST — {{PROJECT}}

> Lỗi/hiện tượng **đang mở**, cần theo dõi qua nhiều ngày.
> - **Chủ dự án** thêm khối `WATCH-*` khi muốn "để mắt" tới một thứ.
> - **Triage** mỗi ngày cập nhật `last_seen` / `count_today` / `trend`, escalate khi vượt `threshold`,
>   và ghi `(đề xuất đóng)` khi vắng ≥ `close_after_days` ngày.
> - ⚠️ Triage **không xoá** khối nào — chỉ đề xuất; người quyết định mới xoá.
>
> File này bị **ghi đè toàn bộ** mỗi lần chạy bằng bản cập nhật của triage → đừng để ghi chú
> quan trọng ngoài các khối (chú thích trong khối thì an toàn, triage được yêu cầu giữ nguyên).

---

## WATCH-001 — <tiêu đề ngắn>
- **status**: open            <!-- open | escalated | (đề xuất đóng) -->
- **severity**: 🟡VỪA         <!-- 🔴CAO | 🟡VỪA | ⚪THẤP -->
- **opened**: YYYY-MM-DD
- **owner**: <ai quan tâm>
- **instances**: <web_*, worker_A, hoặc "tất cả">
- **match**: `<chuỗi/regex nhận diện trong log>`
- **threshold**: `> 5 lần/ngày` → escalate lên 🔴 và nêu ở dòng "Nghiêm trọng nhất"
- **close_after_days**: 7
- **last_seen**: (chưa)
- **count_today**: 0
- **history**: <YYYY-MM-DD:n, ...>   <!-- tối đa 14 ngày gần nhất -->
- **trend**: —                <!-- tăng | giảm | ổn định | — -->
- **note**: <bối cảnh: tại sao theo dõi, đã thử gì>

---

<!--
Thêm khối mới bằng cách copy mẫu trên, tăng ID (WATCH-002, ...).
Khối được đánh dấu (đề xuất đóng) và bạn đồng ý → chuyển thành 1 mục trong KNOWN_ISSUES.md
(nếu đã sửa) rồi xoá khối ở đây.
-->

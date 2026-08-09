# KNOWN_ISSUES — {{PROJECT}}

> **Fix diary**: mỗi lỗi **đã đóng** ghi 1 khối. Mỗi ngày, triage của `system-report` đọc file này
> và kiểm **regression** — "cái đã sửa có tái phát không?". Đây là trí nhớ dài hạn thay cho session state.
>
> Quy tắc: **chỉ ghi lỗi ĐÃ SỬA**. Lỗi đang mở → `WATCHLIST.md`.
> Viết dấu hiệu nhận biết đủ cụ thể để máy grep được trong log (chuỗi lỗi thật, không diễn giải).

---

## ISSUE-001 — <tiêu đề ngắn>
- **Đóng ngày**: YYYY-MM-DD
- **Ảnh hưởng**: <instance/type nào, hậu quả gì>
- **Triệu chứng trong log** (chuỗi để grep, càng đặc trưng càng tốt):
  ```
  <ví dụ: NullReferenceException at OrderService.Settle>
  ```
- **Nguyên nhân gốc**: <1-2 câu>
- **Đã sửa bằng**: `path/file.ext:line` · commit `<sha>`
- **Cách nhận biết tái phát**: <chuỗi trên xuất hiện lại / tần suất > N lần/ngày>
- **Mức nếu tái phát**: 🔴CAO | 🟡VỪA | ⚪THẤP

---

<!--
Copy khối trên cho mỗi lỗi mới đóng. Giữ ID tăng dần, không tái sử dụng ID.
Khối nào quá cũ và chắc chắn không còn hạ tầng liên quan → chuyển xuống mục "Đã lưu trữ" bên dưới
thay vì xoá (để đọc lại lịch sử).
-->

## Đã lưu trữ (không kiểm regression nữa)
- (trống)

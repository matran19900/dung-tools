# ARCHITECTURE — framework portable của `system-report`

> Dành cho **người đọc** muốn hiểu cơ chế trước khi init, hoặc muốn thay 1 mảnh (transport, delivery,
> ngôn ngữ reporter). Bản rút gọn dùng lúc chạy nằm ở `SKILL.md` §3.

## 1. Vấn đề

Một hệ thống nhiều bản chạy (web, worker, db, cron job…), trên nhiều máy, topology thay đổi.
Chủ dự án không muốn ngồi đọc log; họ muốn **mỗi sáng nhận đúng 1 tin nhắn**: hệ thống ổn hay không,
nếu không thì nặng→nhẹ ra sao. Ràng buộc:

- Không được sửa hệ thống thật để lấy telemetry (rủi ro > giá trị).
- Mỗi lần chạy là **một phiên AI mới, không trí nhớ** → phải có trí nhớ ngoài.
- Không ai trực để khởi động lại khi báo cáo hỏng → **im lặng phải là tín hiệu lỗi**.

## 2. Bốn mảnh ghép

```
   ┌──────────────┐   push   ┌───────────────┐   pull   ┌────────────┐   giao   ┌────────┐
   │  Reporter    │ ───────▶ │  Transport    │ ───────▶ │  Runner    │ ───────▶ │ Chủ DA │
   │ (trong app,  │          │ (Redis-log    │          │ + AI triage│          │webhook │
   │  opt-in)     │          │  RIÊNG)       │          │  READ-ONLY │          │ + file │
   └──────────────┘          └───────────────┘          └────────────┘          └────────┘
          │                          │                        │
   1 file + 1 dòng            chỉ REPORT_*            KNOWN_ISSUES + WATCHLIST
   mặc định TẮT               có TTL + auth           (trí nhớ dài hạn)
```

| Mảnh | Sống ở đâu | Thay được không |
|---|---|---|
| Reporter | trong từng instance | có — 4 skeleton (C#/Py/TS/Go), viết thêm dễ vì chỉ cần 3 lệnh Redis |
| Transport | máy riêng / container riêng | có — `redis:` / `file:` / `cmd:` / `http:` per-instance |
| Runner | 1 "triage box" chạy 24/7 | `run.sh` generic, cron gọi |
| Trí nhớ | file trong repo | `KNOWN_ISSUES.md` (đã đóng) + `WATCHLIST.md` (đang mở) |

## 3. Vì sao từng quyết định

**Reporter opt-in, tách file, gỡ 1 nốt.**
Telemetry là thứ **thêm vào** hệ thống đang chạy được. Nếu nó có thể làm sập app, nó không đáng.
Nên: mặc định `REPORT_ENABLED=false` (build có nhưng trơ), toàn bộ code trong **1 file**, wiring
đúng **1 dòng** → gỡ = xoá file + xoá dòng, không còn dấu vết. Mọi exception bị nuốt, hàng đợi có
trần và **drop khi đầy** (thà mất log còn hơn chặn request thật). Zero dependency để không kéo
theo vấn đề version/NuGet/npm vào dự án.

**Transport tách hạ tầng production.**
Nếu đẩy log vào Redis production, một ngày log nhiều bất thường = Redis đầy = sập hệ thống thật.
Redis-log riêng có `maxmemory` + `allkeys-lru` + TTL: nó tự chịu hậu quả của chính nó.
Chỉ namespace `REPORT_*`, có auth (hoặc ACL) → nhìn vào là biết ngay có trỏ nhầm không.

**Pull chứ không push-thẳng-tới-AI.**
Reporter chỉ biết ghi vào 1 key. Nó không biết webhook, không biết AI, không biết định dạng báo cáo.
Đổi cách triage/giao không cần đụng vào bất kỳ instance nào.

**Khám phá instance động (SCAN) + roster đăng ký (SADD).**
`SCAN REPORT_DAILY:{date}:*` bắt được instance **mới** chưa khai báo trong config → scale-out không
cần sửa config. `SMEMBERS REPORT_INSTANCES` giữ danh sách "từng tồn tại" → **hiệu số** giữa hai tập
chính là **instance chết**. Đây là tín hiệu quan trọng nhất mà log-based monitoring hay bỏ sót:
log không có gì ≠ ổn, có thể là **không còn ai ghi log**.

**Trí nhớ 2 lớp thay session state.**
Mỗi run là phiên AI mới. Muốn phát hiện "lỗi này tái phát" hay "cái này tăng dần 3 ngày rồi" thì
trí nhớ phải nằm **ngoài** phiên, ở dạng người cũng đọc/sửa được:
- `KNOWN_ISSUES.md` — lỗi **đã đóng** + chuỗi log để nhận diện → checklist regression.
- `WATCHLIST.md` — lỗi **đang mở** + ngưỡng + lịch sử đếm → escalate / đề xuất đóng.
Người thêm khối, AI cập nhật số liệu. Không cần database.

**AI chỉ in text, runner mới ghi file.**
Ranh giới READ-ONLY dễ nói khó giữ. Nên cắt hẳn khả năng: phiên triage chạy với `--allowedTools
"Read,Grep,Glob"`, output là 3 block có marker (`===DIGEST===` / `===REPORT===` / `===WATCHLIST===`).
`run.sh` parse và ghi. AI **không có** đường ghi file, kể cả WATCHLIST của chính nó.
Thiếu block WATCHLIST → giữ nguyên file cũ (không bao giờ xoá trắng vì một lần AI trả lời lỗi).

**Heartbeat là bất biến, không phải tính năng.**
Ngày sạch vẫn gửi 1 dòng 🟢. Triage timeout/lỗi → runner tự sinh digest 🔴 "TRIAGE FAILED".
Hệ quả: **im lặng luôn có nghĩa là hỏng** (cron chết, máy chết, webhook sai) — người nhận không bao
giờ phải đoán "không có tin là tin tốt hay là hệ thống báo cáo tèo?".

**Lọc log trước khi đưa AI.**
Log 1 ngày có thể hàng trăm nghìn dòng. Runner ưu tiên `ERROR|WARN|FATAL|Exception|panic|timeout…`
kèm ngữ cảnh (−1/+2 dòng), cắt trần theo instance rồi cắt trần tổng. Payload đi qua **stdin**
(không qua argv) để tránh `ARG_MAX`. Khi đã cắt, payload ghi rõ "đã lọc X/Y dòng" để AI biết mình
đang nhìn dữ liệu không đầy đủ và nói ra điều đó thay vì suy đoán.

## 4. Vòng đời một ngày

```
00:00  Reporter các instance ghi REPORT_DAILY:{today}:{name} (APPEND + EXPIRE)
08:07  cron → run.sh
       ├─ git pull (chỉ khi tree sạch)
       ├─ SMEMBERS REPORT_INSTANCES        → đã đăng ký
       ├─ SCAN REPORT_DAILY:{today}:*      → có report hôm nay
       ├─ hiệu số                          → NGHI CHẾT
       ├─ đọc adapter file:/cmd:/http: cho instance ngoài transport
       ├─ lọc + build payload (roster + signals + KNOWN_ISSUES + WATCHLIST + log)
       ├─ claude -p < payload   (timeout, read-only)
       ├─ parse 3 block → ghi docs/system-report/{date}.md + WATCHLIST.md
       └─ POST digest → webhook   |   exit code 0/2/3/4 cho cron
08:08  Chủ dự án đọc 1 tin nhắn trên điện thoại.
```

## 5. Chỗ nối cho người mở rộng

| Muốn | Sửa ở đâu |
|---|---|
| Thêm ngôn ngữ Reporter | copy 1 skeleton; chỉ cần 3 lệnh RESP: `SADD`, `APPEND`, `EXPIRE` |
| Bỏ Redis | đặt `transport.type: none`, mỗi instance dùng `source: file:`/`cmd:`/`https:` |
| Đổi kênh giao | `delivery.webhook_env` (tự nhận Discord/Slack/generic trong `run.sh`) |
| Event real-time | `XADD REPORT_EVENT:{instance}` + 1 nhánh đọc stream trong runner |
| Giảm báo động giả | thêm 1 phiên AI thứ 2 verify đối kháng findings trước khi gửi (xem SKILL §9) |
| Nhiều dự án 1 triage box | mỗi repo có `ops/system-report/config.yml` riêng; cron 1 dòng/dự án |

## 6. Giới hạn đã biết

- **Không real-time**: mặc định 1 lần/ngày. Sự cố lúc 09:00 báo lúc 08:07 hôm sau — cần nhanh hơn
  thì tăng tần suất cron (payload nhỏ hơn) hoặc dùng nhánh `REPORT_EVENT`.
- **Chỉ thấy cái được ghi log**: instance im lặng vì treo mà process còn sống → chỉ bắt được qua
  "vắng report", không phân biệt được treo vs không có việc.
- **AI có thể sai**: mọi finding bắt buộc kèm trích log; `file:line` không tìm được thì ghi
  "chưa correlate được". Người đọc vẫn phải là người quyết định.
- **Không tự sửa gì**: theo thiết kế. Vòng fix do người chủ động (`/roles:cto` → `/roles:em`).

// =============================================================================
// system-report Reporter — TELEMETRY TUỲ CHỌN, MẶC ĐỊNH TẮT.
//
//	Gỡ bỏ hoàn toàn = xoá file này + 1 dòng wiring (`report.Install()`). Hết.
//
// Bật:  REPORT_ENABLED=true  REPORT_INSTANCE={{INSTANCE}}  REPORT_REDIS_HOST=...
// Wiring (đúng 1 dòng, đầu func main):
//
//	report.Install()
//
// BẤT BIẾN (system-report §3.4):
//   - airtight  — recover mọi panic; không bao giờ block caller
//   - opt-in    — REPORT_ENABLED chưa bật thì Install() là no-op tuyệt đối
//   - 1 file    — zero dependency (chỉ stdlib), nói RESP thẳng qua net.Conn
//   - drop-safe — channel có trần; đầy thì VỨT log
//   - chỉ ghi namespace REPORT_* trên 1 Redis-log RIÊNG (không phải Redis production)
//
// Đổi `package report` cho khớp layout dự án (vd đặt trong package main).
// =============================================================================
package report

import (
	"fmt"
	"log"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	enabled  = truthy(getenv("REPORT_ENABLED", "false"))
	instance = getenv("REPORT_INSTANCE", hostname())
	host     = getenv("REPORT_REDIS_HOST", "127.0.0.1")
	port     = getenv("REPORT_REDIS_PORT", "6399")
	db       = getenv("REPORT_REDIS_DB", "0")
	pass     = getenv("REPORT_REDIS_PASS", "")
	ttlDays  = atoi(getenv("REPORT_TTL_DAYS", "7"), 7)
	flushSec = atoi(getenv("REPORT_FLUSH_SEC", "5"), 5)
	queueMax = atoi(getenv("REPORT_QUEUE_MAX", "2000"), 2000)

	// cấp phát ngay lúc init: tránh data race giữa Log() và Install()
	ch      = makeCh()
	once    sync.Once
	dropped int64
	mu      sync.Mutex
)

func makeCh() chan string {
	if !enabled {
		return nil
	}
	return make(chan string, queueMax)
}

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func truthy(s string) bool {
	switch strings.ToLower(s) {
	case "1", "true", "yes":
		return true
	}
	return false
}
func atoi(s string, d int) int {
	if n, err := strconv.Atoi(s); err == nil {
		return n
	}
	return d
}
func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return h
}

// ── RESP tối giản ────────────────────────────────────────────────────────────
func writeCmd(c net.Conn, args ...string) error {
	var b strings.Builder
	fmt.Fprintf(&b, "*%d\r\n", len(args))
	for _, a := range args {
		fmt.Fprintf(&b, "$%d\r\n%s\r\n", len(a), a)
	}
	_ = c.SetWriteDeadline(time.Now().Add(5 * time.Second))
	_, err := c.Write([]byte(b.String()))
	return err
}

func dial() (net.Conn, error) {
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), 5*time.Second)
	if err != nil {
		return nil, err
	}
	if pass != "" {
		if err := writeCmd(c, "AUTH", pass); err != nil {
			c.Close()
			return nil, err
		}
	}
	if db != "0" {
		if err := writeCmd(c, "SELECT", db); err != nil {
			c.Close()
			return nil, err
		}
	}
	go func() { // nuốt reply để kernel buffer không đầy
		buf := make([]byte, 4096)
		for {
			_ = c.SetReadDeadline(time.Now().Add(24 * time.Hour))
			if _, err := c.Read(buf); err != nil {
				return
			}
		}
	}()
	return c, nil
}

func worker() {
	defer func() { _ = recover() }()
	var conn net.Conn
	registered := false
	tick := time.NewTicker(time.Duration(flushSec) * time.Second)
	defer tick.Stop()
	batch := make([]string, 0, 200)

	send := func() {
		if len(batch) == 0 {
			return
		}
		defer func() { _ = recover() }()
		if conn == nil {
			c, err := dial()
			if err != nil {
				batch = batch[:0] // không giữ log cũ vô hạn
				return
			}
			conn, registered = c, false
		}
		key := "REPORT_DAILY:" + time.Now().Format("2006-01-02") + ":" + instance
		var err error
		if !registered {
			if err = writeCmd(conn, "SADD", "REPORT_INSTANCES", instance); err == nil {
				registered = true
			}
		}
		if err == nil {
			err = writeCmd(conn, "APPEND", key, strings.Join(batch, "\n")+"\n")
		}
		if err == nil {
			err = writeCmd(conn, "EXPIRE", key, strconv.Itoa(ttlDays*86400))
		}
		if err != nil {
			conn.Close()
			conn, registered = nil, false
		}
		batch = batch[:0]
	}

	for {
		select {
		case line, ok := <-ch:
			if !ok {
				send()
				return
			}
			batch = append(batch, line)
			if len(batch) >= 200 {
				send()
			}
		case <-tick.C:
			send()
		}
	}
}

// Log đẩy 1 dòng vào báo cáo ngày. Không bao giờ panic, không bao giờ block.
func Log(line string) {
	if !enabled || ch == nil {
		return
	}
	defer func() { _ = recover() }()
	msg := time.Now().Format("15:04:05") + " [" + instance + "] " + strings.ReplaceAll(line, "\r", " ")
	select {
	case ch <- msg:
	default: // hàng đợi đầy → vứt, KHÔNG chặn app
		mu.Lock()
		dropped++
		mu.Unlock()
	}
}

// Logf tiện cho call site có format.
func Logf(format string, a ...any) { Log(fmt.Sprintf(format, a...)) }

type teeWriter struct{ next *os.File }

func (t teeWriter) Write(p []byte) (int, error) {
	Log(strings.TrimRight(string(p), "\n"))
	return t.next.Write(p)
}

// Install là 1 dòng wiring duy nhất. Chưa bật REPORT_ENABLED → no-op tuyệt đối.
func Install() {
	if !enabled {
		return
	}
	once.Do(func() {
		defer func() { _ = recover() }()
		go worker()
		log.SetOutput(teeWriter{next: os.Stderr}) // mirror log chuẩn vào báo cáo
		Log("instance started")
	})
}

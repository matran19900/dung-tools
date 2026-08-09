// =============================================================================
// system-report Reporter — TELEMETRY TUỲ CHỌN, MẶC ĐỊNH TẮT.
//
//   Gỡ bỏ hoàn toàn = xoá file này + 1 dòng wiring (`Report.Install();`). Hết.
//
// Bật:  REPORT_ENABLED=true  REPORT_INSTANCE={{INSTANCE}}  REPORT_REDIS_HOST=...
// Wiring (chọn ĐÚNG MỘT dòng):
//   · console/worker/service :  Report.Install();
//   · ASP.NET Core (DI)      :  builder.Logging.AddProvider(Report.Provider());
//     → nhánh này cần bật symbol MICROSOFT_EXTENSIONS_LOGGING (thêm vào .csproj:
//       <DefineConstants>$(DefineConstants);MICROSOFT_EXTENSIONS_LOGGING</DefineConstants>).
//       Không bật → dùng Report.Install() như trên.
//
// BẤT BIẾN (system-report §3.4):
//   · airtight  — nuốt MỌI exception; không throw ra caller, không block
//   · opt-in    — REPORT_ENABLED chưa bật thì Install()/Provider() là no-op tuyệt đối
//   · 1 file    — zero NuGet package, nói RESP thẳng qua TcpClient
//   · drop-safe — hàng đợi có trần; đầy thì VỨT log
//   · chỉ ghi namespace REPORT_* trên 1 Redis-log RIÊNG (không phải Redis production)
//
// Đổi namespace cho khớp dự án. Yêu cầu: .NET 6+.
// =============================================================================
using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Ops.SystemReport
{
    public static class Report
    {
        private static readonly bool Enabled =
            (Environment.GetEnvironmentVariable("REPORT_ENABLED") ?? "false")
            .Trim().ToLowerInvariant() is "1" or "true" or "yes";

        private static readonly string Instance =
            Env("REPORT_INSTANCE", Environment.MachineName);
        private static readonly string Host = Env("REPORT_REDIS_HOST", "127.0.0.1");
        private static readonly int Port = EnvInt("REPORT_REDIS_PORT", 6399);
        private static readonly string Db = Env("REPORT_REDIS_DB", "0");
        private static readonly string Pass = Env("REPORT_REDIS_PASS", "");
        private static readonly int TtlSec = EnvInt("REPORT_TTL_DAYS", 7) * 86400;
        private static readonly int FlushSec = EnvInt("REPORT_FLUSH_SEC", 5);
        private static readonly int QueueMax = EnvInt("REPORT_QUEUE_MAX", 2000);

        private static readonly BlockingCollection<string> Queue =
            new BlockingCollection<string>(new ConcurrentQueue<string>(), Math.Max(16, QueueMax));

        private static int _started;
        private static long _dropped;

        private static string Env(string k, string d)
        {
            var v = Environment.GetEnvironmentVariable(k);
            return string.IsNullOrWhiteSpace(v) ? d : v;
        }

        private static int EnvInt(string k, int d) =>
            int.TryParse(Environment.GetEnvironmentVariable(k), out var n) ? n : d;

        /// <summary>Đẩy 1 dòng vào báo cáo ngày. Không bao giờ throw, không bao giờ block.</summary>
        public static void Log(string line)
        {
            if (!Enabled) return;
            try
            {
                var msg = DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture)
                          + " [" + Instance + "] " + (line ?? string.Empty).Replace("\r", " ");
                if (!Queue.TryAdd(msg)) Interlocked.Increment(ref _dropped);
            }
            catch { /* telemetry KHÔNG được làm phiền hệ thống */ }
        }

        /// <summary>1 dòng wiring duy nhất. Chưa bật REPORT_ENABLED → no-op tuyệt đối.</summary>
        public static void Install()
        {
            if (!Enabled) return;
            if (Interlocked.Exchange(ref _started, 1) == 1) return;
            try
            {
                Task.Factory.StartNew(PumpAsync, TaskCreationOptions.LongRunning);
                AppDomain.CurrentDomain.UnhandledException += (_, e) =>
                    Log("FATAL UnhandledException: " + (e.ExceptionObject as Exception)?.ToString());
                TaskScheduler.UnobservedTaskException += (_, e) =>
                {
                    Log("FATAL UnobservedTaskException: " + e.Exception);
                    e.SetObserved();
                };
                AppDomain.CurrentDomain.ProcessExit += (_, _) =>
                {
                    Log("instance stopping (dropped=" + Interlocked.Read(ref _dropped) + ")");
                    Queue.CompleteAdding();
                    Thread.Sleep(500); // cho worker 1 nhịp flush cuối; không chặn lâu
                };
                Log("instance started");
            }
            catch { /* airtight */ }
        }

        // ── RESP tối giản qua TcpClient ──────────────────────────────────────
        private static async Task PumpAsync()
        {
            TcpClient client = null;
            NetworkStream stream = null;
            var registered = false;
            var batch = new System.Collections.Generic.List<string>(256);

            while (true)
            {
                try
                {
                    batch.Clear();
                    if (!Queue.TryTake(out var first, Timeout.Infinite)) break; // CompleteAdding
                    batch.Add(first);
                    var deadline = Environment.TickCount64 + FlushSec * 1000L;
                    while (batch.Count < 200)
                    {
                        var left = (int)(deadline - Environment.TickCount64);
                        if (left <= 0 || !Queue.TryTake(out var more, left)) break;
                        batch.Add(more);
                    }

                    if (client == null || !client.Connected)
                    {
                        client?.Dispose();
                        client = new TcpClient { NoDelay = true };
                        await client.ConnectAsync(Host, Port).ConfigureAwait(false);
                        stream = client.GetStream();
                        registered = false;
                        if (Pass.Length > 0) await SendAsync(stream, "AUTH", Pass).ConfigureAwait(false);
                        if (Db != "0") await SendAsync(stream, "SELECT", Db).ConfigureAwait(false);
                        _ = Task.Run(() => DrainAsync(stream)); // nuốt reply
                    }

                    var key = "REPORT_DAILY:" + DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
                              + ":" + Instance;
                    if (!registered)
                    {
                        await SendAsync(stream, "SADD", "REPORT_INSTANCES", Instance).ConfigureAwait(false);
                        registered = true;
                    }
                    await SendAsync(stream, "APPEND", key, string.Join("\n", batch) + "\n").ConfigureAwait(false);
                    await SendAsync(stream, "EXPIRE", key, TtlSec.ToString(CultureInfo.InvariantCulture))
                        .ConfigureAwait(false);
                }
                catch (InvalidOperationException) { break; } // queue đã CompleteAdding + rỗng
                catch
                {
                    try { client?.Dispose(); } catch { }
                    client = null; stream = null; registered = false;
                    try { await Task.Delay(2000).ConfigureAwait(false); } catch { }
                }
            }
            try { client?.Dispose(); } catch { }
        }

        private static async Task SendAsync(NetworkStream s, params string[] args)
        {
            var sb = new StringBuilder().Append('*').Append(args.Length).Append("\r\n");
            foreach (var a in args)
                sb.Append('$').Append(Encoding.UTF8.GetByteCount(a)).Append("\r\n").Append(a).Append("\r\n");
            var bytes = Encoding.UTF8.GetBytes(sb.ToString());
            await s.WriteAsync(bytes, 0, bytes.Length).ConfigureAwait(false);
        }

        private static async Task DrainAsync(NetworkStream s)
        {
            var buf = new byte[4096];
            try { while (await s.ReadAsync(buf, 0, buf.Length).ConfigureAwait(false) > 0) { } }
            catch { /* ignore */ }
        }

        // ── Tuỳ chọn: ILoggerProvider cho ASP.NET Core ───────────────────────
        // Bỏ nguyên vùng #if nếu dự án không dùng Microsoft.Extensions.Logging.
#if MICROSOFT_EXTENSIONS_LOGGING
        public static Microsoft.Extensions.Logging.ILoggerProvider Provider()
        {
            Install();
            return new ReportLoggerProvider();
        }

        private sealed class ReportLoggerProvider : Microsoft.Extensions.Logging.ILoggerProvider
        {
            public Microsoft.Extensions.Logging.ILogger CreateLogger(string name) => new ReportLogger(name);
            public void Dispose() { }
        }

        private sealed class ReportLogger : Microsoft.Extensions.Logging.ILogger
        {
            private readonly string _name;
            public ReportLogger(string name) { _name = name; }
            public IDisposable BeginScope<TState>(TState state) => null;
            public bool IsEnabled(Microsoft.Extensions.Logging.LogLevel level) =>
                Enabled && level >= Microsoft.Extensions.Logging.LogLevel.Warning;
            public void Log<TState>(Microsoft.Extensions.Logging.LogLevel level,
                Microsoft.Extensions.Logging.EventId id, TState state, Exception ex,
                Func<TState, Exception, string> fmt)
            {
                if (!IsEnabled(level)) return;
                Report.Log(level + " " + _name + ": " + fmt(state, ex) + (ex != null ? " | " + ex : ""));
            }
        }
#else
        /// <summary>Stub khi chưa bật MICROSOFT_EXTENSIONS_LOGGING — dùng Install() thay thế.</summary>
        public static object Provider() { Install(); return null; }
#endif
    }
}

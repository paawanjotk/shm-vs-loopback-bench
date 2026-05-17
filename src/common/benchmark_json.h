#pragma once

#include "latency_stats.h"

#include <fmt/core.h>
#include <string>
#include <string_view>

struct BenchmarkResultPayload {
    int schema_version = 1;
    std::string run_id;
    std::string role;
    std::string transport;  // "shm" | "socket"
    std::string bench_mode;
    size_t warmup_messages = 0;
    size_t measure_messages = 0;
    uint64_t total_received = 0;
    size_t measured_samples = 0;
    size_t queue_capacity_slots = 0;
    int socket_rcvbuf_bytes = -1;  // -1 if not applicable
    double cycles_per_ns = 0.0;
    LatencySummaryNs latency{};
    double wall_seconds = 0.0;
    bool shm_handoff_latency = false;  // true when SHM used pause+drain (bench_mode=benchmark)
};

inline std::string json_escape(std::string_view s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                out += c;
                break;
        }
    }
    return out;
}

inline std::string benchmark_result_to_json(const BenchmarkResultPayload& p) {
    const double tput =
        (p.wall_seconds > 0.0) ? (static_cast<double>(p.measured_samples) / p.wall_seconds) : 0.0;

    return fmt::format(
        "{{"
        "\"schema_version\":{},"
        "\"run_id\":\"{}\","
        "\"role\":\"{}\","
        "\"transport\":\"{}\","
        "\"bench_mode\":\"{}\","
        "\"warmup_messages\":{},"
        "\"measure_messages\":{},"
        "\"total_received\":{},"
        "\"measured_samples\":{},"
        "\"queue_capacity_slots\":{},"
        "\"socket_rcvbuf_bytes\":{},"
        "\"cycles_per_ns\":{:.9f},"
        "\"latency_ns\":{{\"min\":{:.6f},\"p50\":{:.6f},\"p99\":{:.6f},\"p999\":{:.6f},\"max\":{:.6f}}},"
        "\"wall_seconds\":{:.9f},"
        "\"throughput_messages_per_sec\":{:.6f},"
        "\"shm_handoff_latency\":{}"
        "}}",
        p.schema_version,
        json_escape(p.run_id),
        json_escape(p.role),
        json_escape(p.transport),
        json_escape(p.bench_mode),
        p.warmup_messages,
        p.measure_messages,
        p.total_received,
        p.measured_samples,
        p.queue_capacity_slots,
        p.socket_rcvbuf_bytes,
        p.cycles_per_ns,
        p.latency.min_ns,
        p.latency.p50_ns,
        p.latency.p99_ns,
        p.latency.p999_ns,
        p.latency.max_ns,
        p.wall_seconds,
        tput,
        p.shm_handoff_latency ? "true" : "false");
}

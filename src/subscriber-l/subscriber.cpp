#include "subscriber.h"
#include "../common/benchmark_json.h"
#include "../common/latency_stats.h"
#include "../common/quote.h"
#include "../common/ringbuffer.h"
#include "../common/tsc_clock.h"

#include <chrono>
#include <cstring>
#include <fmt/core.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

namespace {
constexpr size_t kWarmupMessages = 100000;
constexpr size_t kMeasureMessages = 1000000;

bool read_full(int fd, void* buffer, size_t bytes) {
    auto* out = static_cast<uint8_t*>(buffer);
    size_t total = 0;
    while (total < bytes) {
        const ssize_t n = read(fd, out + total, bytes - total);
        if (n <= 0) {
            return false;
        }
        total += static_cast<size_t>(n);
    }
    return true;
}
}

SubscriberLoopback::SubscriberLoopback() {}

SubscriberLoopback::~SubscriberLoopback() {
    disconnect();
}

bool SubscriberLoopback::connect() {
    // Create Unix domain socket
    socket_fd_ = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socket_fd_ == -1) {
        fmt::print(stderr, "[SubscriberLoopback] Failed to create socket\n");
        return false;
    }

    int rcvbuf = static_cast<int>(kMarketQueueSize * sizeof(MarketMessageData));
    if (setsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) == -1) {
        fmt::print(stderr, "[SubscriberLoopback] Warning: setsockopt SO_RCVBUF failed\n");
    }
    int actual_rcvbuf = 0;
    socklen_t optlen = sizeof(actual_rcvbuf);
    getsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &actual_rcvbuf, &optlen);
    fmt::print(stderr, "[SubscriberLoopback] SO_RCVBUF={} bytes\n", actual_rcvbuf);

    // Setup socket address
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    
    // Retry connection with waiting
    const int MAX_RETRIES = 30;  // 30 attempts
    const int RETRY_DELAY_MS = 500;  // 500ms between attempts = 15 seconds total
    
    for (int attempt = 1; attempt <= MAX_RETRIES; ++attempt) {
        fmt::print(stderr, "[SubscriberLoopback] Attempting to connect to {} (attempt {}/{})\n",
                   SOCKET_PATH, attempt, MAX_RETRIES);
        
        if (::connect(socket_fd_, (struct sockaddr*)&addr, sizeof(addr)) == 0) {
            connected_ = true;
            fmt::print(stderr, "[SubscriberLoopback] Successfully connected to market data socket at {}\n", SOCKET_PATH);
            return true;
        }
        
        if (attempt < MAX_RETRIES) {
            std::this_thread::sleep_for(std::chrono::milliseconds(RETRY_DELAY_MS));
        }
    }
    
    fmt::print(stderr, "[SubscriberLoopback] Failed to connect after {} attempts\n", MAX_RETRIES);
    close(socket_fd_);
    socket_fd_ = -1;
    return false;
}

bool SubscriberLoopback::receive(MarketMessageData& data, int64_t& now) {
    if (!connected_) {
        return false;
    }

    if (!read_full(socket_fd_, &data, sizeof(MarketMessageData))) {
        connected_ = false;
        return false;
    }

    now = rdtsc_ordered();
    return true;
}

void SubscriberLoopback::disconnect() {
    if (socket_fd_ != -1) {
        close(socket_fd_);
        socket_fd_ = -1;
    }
    connected_ = false;
    fmt::print(stderr, "[SubscriberLoopback] Disconnected from market data socket\n");
}

bool SubscriberLoopback::is_connected() const {
    return connected_;
}

void SubscriberLoopback::run(const BenchmarkOptions& options) {
    fmt::print(stderr, "[SubscriberLoopback] Starting subscriber-loopback...\n");

    if (!connect()) {
        fmt::print(stderr, "[SubscriberLoopback] Failed to connect to publisher\n");
        return;
    }

    int actual_rcvbuf = 0;
    {
        socklen_t optlen = sizeof(actual_rcvbuf);
        if (getsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &actual_rcvbuf, &optlen) == -1) {
            actual_rcvbuf = -1;
        }
    }

    const TSCClock tsc = init_tsc_clock();
    MarketMessageData data;
    int64_t now_cycles = 0;
    uint64_t message_count = 0;
    std::vector<uint64_t> latency_samples;
    latency_samples.reserve(kMeasureMessages);

    using clock = std::chrono::steady_clock;
    clock::time_point wall_start{};
    bool wall_started = false;

    while (is_connected()) {
        if (receive(data, now_cycles)) {
            ++message_count;
            if (message_count <= kWarmupMessages) {
                continue;
            }

            if (!wall_started) {
                wall_start = clock::now();
                wall_started = true;
            }
            latency_samples.push_back(static_cast<uint64_t>(now_cycles) - data.send_timestamp);

            if (latency_samples.size() % 100000 == 0) {
                fmt::print(stderr, "[SubscriberLoopback] measured={} warmup={}\n", latency_samples.size(),
                           kWarmupMessages);
            }

            if (latency_samples.size() >= kMeasureMessages) {
                break;
            }
        }
    }

    const clock::time_point wall_end = clock::now();
    const double wall_seconds =
        wall_started ? std::chrono::duration<double>(wall_end - wall_start).count() : 0.0;

    LatencySummaryNs summary = summarize_cycles(latency_samples, tsc.cycles_per_ns);

    if (options.json_output) {
        BenchmarkResultPayload payload;
        payload.run_id = options.run_id;
        payload.role = "subscriber-socket";
        payload.transport = "socket";
        payload.bench_mode = options.bench_mode;
        payload.warmup_messages = kWarmupMessages;
        payload.measure_messages = kMeasureMessages;
        payload.total_received = message_count;
        payload.measured_samples = latency_samples.size();
        payload.queue_capacity_slots = kMarketQueueSize;
        payload.socket_rcvbuf_bytes = actual_rcvbuf;
        payload.cycles_per_ns = tsc.cycles_per_ns;
        payload.latency = summary;
        payload.wall_seconds = wall_seconds;
        fmt::print(stdout, "{}\n", benchmark_result_to_json(payload));
    } else {
        print_summary("socket", latency_samples.size(), summary);
    }
    fmt::print(stderr, "[SubscriberLoopback] total_received={} warmup={} measured={}\n", message_count,
               kWarmupMessages, latency_samples.size());
    disconnect();
}

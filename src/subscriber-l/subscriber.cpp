#include "subscriber.h"
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
        fmt::print("[SubscriberLoopback] Failed to create socket\n");
        return false;
    }

    int rcvbuf = static_cast<int>(kMarketQueueSize * sizeof(MarketMessageData));
    if (setsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) == -1) {
        fmt::print("[SubscriberLoopback] Warning: setsockopt SO_RCVBUF failed\n");
    }
    int actual_rcvbuf = 0;
    socklen_t optlen = sizeof(actual_rcvbuf);
    getsockopt(socket_fd_, SOL_SOCKET, SO_RCVBUF, &actual_rcvbuf, &optlen);
    fmt::print("[SubscriberLoopback] SO_RCVBUF={} bytes\n", actual_rcvbuf);

    // Setup socket address
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    
    // Retry connection with waiting
    const int MAX_RETRIES = 30;  // 30 attempts
    const int RETRY_DELAY_MS = 500;  // 500ms between attempts = 15 seconds total
    
    for (int attempt = 1; attempt <= MAX_RETRIES; ++attempt) {
        fmt::print("[SubscriberLoopback] Attempting to connect to {} (attempt {}/{})\n", 
                   SOCKET_PATH, attempt, MAX_RETRIES);
        
        if (::connect(socket_fd_, (struct sockaddr*)&addr, sizeof(addr)) == 0) {
            connected_ = true;
            fmt::print("[SubscriberLoopback] Successfully connected to market data socket at {}\n", SOCKET_PATH);
            return true;
        }
        
        if (attempt < MAX_RETRIES) {
            std::this_thread::sleep_for(std::chrono::milliseconds(RETRY_DELAY_MS));
        }
    }
    
    fmt::print("[SubscriberLoopback] Failed to connect after {} attempts\n", MAX_RETRIES);
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
    fmt::print("[SubscriberLoopback] Disconnected from market data socket\n");
}

bool SubscriberLoopback::is_connected() const {
    return connected_;
}

void SubscriberLoopback::run() {
    fmt::print("[SubscriberLoopback] Starting subscriber-loopback...\n");

    if (!connect()) {
        fmt::print("[SubscriberLoopback] Failed to connect to publisher\n");
        return;
    }

    const TSCClock tsc = init_tsc_clock();
    MarketMessageData data;
    int64_t now_cycles = 0;
    uint64_t message_count = 0;
    std::vector<uint64_t> latency_samples;
    latency_samples.reserve(kMeasureMessages);

    while (is_connected()) {
        if (receive(data, now_cycles)) {
            ++message_count;
            if (message_count <= kWarmupMessages) {
                continue;
            }

            latency_samples.push_back(static_cast<uint64_t>(now_cycles) - data.send_timestamp);

            if (latency_samples.size() % 100000 == 0) {
                fmt::print("[SubscriberLoopback] measured={} warmup={}\n", latency_samples.size(), kWarmupMessages);
            }

            if (latency_samples.size() >= kMeasureMessages) {
                break;
            }
        }
    }

    LatencySummaryNs summary = summarize_cycles(latency_samples, tsc.cycles_per_ns);
    print_summary("socket", latency_samples.size(), summary);
    fmt::print("[SubscriberLoopback] total_received={} warmup={} measured={}\n",
               message_count,
               kWarmupMessages,
               latency_samples.size());
    disconnect();
}

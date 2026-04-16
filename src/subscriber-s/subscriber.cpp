#include "subscriber.h"
#include "../common/latency_stats.h"
#include "../common/quote.h"
#include "../common/ringbuffer.h"
#include "../common/tsc_clock.h"

#include <chrono>
#include <cstring>
#include <fmt/core.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>

namespace {
constexpr size_t kWarmupMessages = 10000;
constexpr size_t kMeasureMessages = 200000;
}

SubscriberSharedMemory::SubscriberSharedMemory() {}

SubscriberSharedMemory::~SubscriberSharedMemory() {
    disconnect();
}

bool SubscriberSharedMemory::connect() {
    // Retry opening shared memory with waiting
    const int MAX_RETRIES = 30;  // 30 attempts
    const int RETRY_DELAY_MS = 500;  // 500ms between attempts = 15 seconds total
    
    for (int attempt = 1; attempt <= MAX_RETRIES; ++attempt) {
        fmt::print("[SubscriberSharedMemory] Attempting to open shared memory {} (attempt {}/{})\n",
                   SHARED_MEMORY_NAME, attempt, MAX_RETRIES);
        
        shm_fd_ = shm_open(SHARED_MEMORY_NAME, O_RDWR, 0666);
        if (shm_fd_ != -1) {
            break;  // Successfully opened
        }
        
        if (attempt < MAX_RETRIES) {
            std::this_thread::sleep_for(std::chrono::milliseconds(RETRY_DELAY_MS));
        }
    }
    
    if (shm_fd_ == -1) {
        fmt::print("[SubscriberSharedMemory] Failed to open shared memory after {} attempts\n", MAX_RETRIES);
        return false;
    }
    
    fmt::print("[SubscriberSharedMemory] Successfully opened shared memory: {}\n", SHARED_MEMORY_NAME);
    
    // Map shared memory
    size_t shm_size = sizeof(SharedMarketDataRegion);
    fmt::print("[SubscriberSharedMemory] Mapping {} bytes...\n", shm_size);
    
    shm_ptr_ = mmap(nullptr, shm_size, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd_, 0);
    
    if (shm_ptr_ == MAP_FAILED) {
        fmt::print("[SubscriberSharedMemory] Failed to map shared memory\n");
        close(shm_fd_);
        shm_fd_ = -1;
        return false;
    }
    
    auto* region = reinterpret_cast<SharedMarketDataRegion*>(shm_ptr_);
    constexpr int MAX_READY_RETRIES = 100;
    for (int i = 0; i < MAX_READY_RETRIES; ++i) {
        if (region->ready.load(std::memory_order_acquire) == 1) {
            connected_ = true;
            fmt::print("[SubscriberSharedMemory] Queue ready\n");
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    fmt::print("[SubscriberSharedMemory] Timed out waiting for queue ready flag\n");
    munmap(shm_ptr_, shm_size);
    shm_ptr_ = nullptr;
    close(shm_fd_);
    shm_fd_ = -1;
    return false;
}

bool SubscriberSharedMemory::read(MarketMessageData& data) {
    if (!connected_ || !shm_ptr_) {
        return false;
    }
    // printf("here\n");
    auto* region = reinterpret_cast<SharedMarketDataRegion*>(shm_ptr_);
    return region->queue.pop(data);
}

void SubscriberSharedMemory::disconnect() {
    if (shm_ptr_ != nullptr) {
        size_t shm_size = sizeof(SharedMarketDataRegion);
        munmap(shm_ptr_, shm_size);
        shm_ptr_ = nullptr;
    }
    
    if (shm_fd_ != -1) {
        close(shm_fd_);
        shm_fd_ = -1;
    }
    
    connected_ = false;
    fmt::print("[SubscriberSharedMemory] Disconnected from shared memory\n");
}

bool SubscriberSharedMemory::is_connected() const {
    return connected_;
}

void SubscriberSharedMemory::run() {
    fmt::print("[SubscriberSharedMemory] Starting subscriber-shared-memory...\n");
    
    if (!connect()) {
        fmt::print("[SubscriberSharedMemory] Failed to connect to publisher shared memory\n");
        return;
    }
    
    const TSCClock tsc = init_tsc_clock();
    MarketMessageData data;
    uint64_t message_count = 0;
    std::vector<uint64_t> latency_samples;
    latency_samples.reserve(kMeasureMessages);

    while (is_connected()) {
        if (read(data)) {
            uint64_t t_sub_shm = rdtsc_ordered();
            uint64_t cycles = t_sub_shm - data.shm_timestamp;
            ++message_count;
            if (message_count <= kWarmupMessages) {
                continue;
            }

            latency_samples.push_back(cycles);
            if (latency_samples.size() % 100000 == 0) {
                fmt::print("[SubscriberSharedMemory] measured={} warmup={}\n", latency_samples.size(), kWarmupMessages);
            }
            if (latency_samples.size() >= kMeasureMessages) {
                break;
            }
        } else {
            __builtin_ia32_pause();
        }
    }

    LatencySummaryNs summary = summarize_cycles(latency_samples, tsc.cycles_per_ns);
    print_summary("shm", latency_samples.size(), summary);
    fmt::print("[SubscriberSharedMemory] total_received={} warmup={} measured={}\n",
               message_count,
               kWarmupMessages,
               latency_samples.size());
    disconnect();
}

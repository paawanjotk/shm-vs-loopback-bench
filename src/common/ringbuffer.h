#pragma once

#include <stddef.h>
#include <atomic>
#include "quote.h"

constexpr size_t kMarketQueueSize = 8192;

template <typename T, size_t size>
class SPSCQueue {
    static_assert((size & (size - 1)) == 0, "size must be a power of two");

    alignas(64) std::atomic<size_t> w{0};
    alignas(64) std::atomic<size_t> r{0};
    T buffer[size];

    public:
        bool push(const T& item);
        bool pop(T& item);
        size_t Size();
        bool empty();
};

struct SharedMarketDataRegion {
    std::atomic<uint32_t> ready{0};
    std::atomic<uint32_t> consumer_present{0};
    /// When non-zero, publisher-shm must not enqueue (handoff benchmark drain).
    std::atomic<uint32_t> pause_publish{0};
    alignas(64) SPSCQueue<MarketMessageData, kMarketQueueSize> queue;
};

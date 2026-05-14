#pragma once

#include "../common/benchmark_options.h"
#include "../common/quote.h"
#include "../common/ringbuffer.h"
#include <cstddef>

class SubscriberSharedMemory {
public:
    static constexpr const char* SHARED_MEMORY_NAME = "tryhard";
    static constexpr size_t QUEUE_SIZE = kMarketQueueSize;
    
    SubscriberSharedMemory();
    ~SubscriberSharedMemory();
    
    // Connect to shared memory
    bool connect();
    
    // Read market data from shared memory queue
    bool read(MarketMessageData& data);
    
    // Run subscriber loop (options from CLI: --json --run-id= --bench-mode=)
    void run(const BenchmarkOptions& options = BenchmarkOptions{});
    
    // Disconnect from shared memory
    void disconnect();
    
    // Check if connected
    bool is_connected() const;
    
private:
    void* shm_ptr_ = nullptr;
    int shm_fd_ = -1;
    bool connected_ = false;
    
    template <typename T, size_t size>
    using Queue = SPSCQueue<T, size>;
};

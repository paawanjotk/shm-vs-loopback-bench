#pragma once

#include "../common/benchmark_options.h"
#include "../common/quote.h"
#include <string>

class SubscriberLoopback {
public:
    static constexpr const char* SOCKET_PATH = "/tmp/market.sock";
    
    SubscriberLoopback();
    ~SubscriberLoopback();
    
    // Connect to market data socket
    bool connect();
    
    // Receive market data from socket
    bool receive(MarketMessageData& data, int64_t& now);
    
    void run(const BenchmarkOptions& options = BenchmarkOptions{});
    
    // Disconnect from socket
    void disconnect();
    
    // Check if connected
    bool is_connected() const;
    
private:
    int socket_fd_ = -1;
    bool connected_ = false;
};

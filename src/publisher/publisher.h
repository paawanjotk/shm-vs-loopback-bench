#pragma once

#include "../common/ringbuffer.h"
#include "../common/quote.h"

enum class PublisherMode {
    SHM_ONLY,
    SOCKET_ONLY,
    BOTH
};

class Publisher {
    public:
        PublisherMode mode_;
        explicit Publisher(PublisherMode mode);
        void run();
        SharedMarketDataRegion* create_shared_region(const char* name);
};
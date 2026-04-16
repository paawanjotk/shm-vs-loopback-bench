#pragma once

#include <stddef.h>
#include <atomic>
#include <cstdint>

enum class Instrument : uint16_t {
    RELIANCE,
    TCS,
    INFY
};

struct MarketMessageData{
    uint64_t send_timestamp;
    uint64_t shm_timestamp;
    uint32_t ask;
    uint32_t bid;
    Instrument instrument;
};

static_assert(sizeof(MarketMessageData) == 32, "MarketMessageData layout must stay stable");
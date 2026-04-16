#pragma once

#include <cstdint>

struct TSCClock {
    double cycles_per_ns;
};

uint64_t rdtsc_ordered();
bool pin_to_cpu(int cpu_id);
TSCClock init_tsc_clock();

#include "tsc_clock.h"

#include <chrono>
#include <thread>
#include <x86intrin.h>
#include <sched.h>

using namespace std::chrono;

uint64_t rdtsc_ordered() {
    unsigned aux;
    return __rdtscp(&aux);
}

bool pin_to_cpu(int cpu_id) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu_id, &set);
    return sched_setaffinity(0, sizeof(set), &set) == 0;
}

TSCClock init_tsc_clock() {
    auto t0 = steady_clock::now();
    uint64_t c0 = rdtsc_ordered();

    std::this_thread::sleep_for(milliseconds(500));

    auto t1 = steady_clock::now();
    uint64_t c1 = rdtsc_ordered();
    uint64_t ns = duration_cast<nanoseconds>(t1 - t0).count();
    double cycles_per_ns = double(c1 - c0) / double(ns);

    return {cycles_per_ns};
}

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>
#include <fmt/core.h>

struct LatencySummaryNs {
    double min_ns{0.0};
    double p50_ns{0.0};
    double p99_ns{0.0};
    double p999_ns{0.0};
    double max_ns{0.0};
};

inline size_t percentile_index(size_t count, double percentile) {
    if (count == 0) {
        return 0;
    }
    const double pos = percentile * static_cast<double>(count - 1);
    return static_cast<size_t>(pos);
}

inline LatencySummaryNs summarize_cycles(std::vector<uint64_t>& samples, double cycles_per_ns) {
    LatencySummaryNs summary;
    if (samples.empty() || cycles_per_ns <= 0.0) {
        return summary;
    }

    std::sort(samples.begin(), samples.end());
    const size_t count = samples.size();
    const auto to_ns = [cycles_per_ns](uint64_t cycles) {
        return static_cast<double>(cycles) / cycles_per_ns;
    };

    summary.min_ns = to_ns(samples.front());
    summary.p50_ns = to_ns(samples[percentile_index(count, 0.50)]);
    summary.p99_ns = to_ns(samples[percentile_index(count, 0.99)]);
    summary.p999_ns = to_ns(samples[percentile_index(count, 0.999)]);
    summary.max_ns = to_ns(samples.back());
    return summary;
}

inline void print_summary(const std::string& transport, size_t sample_count, const LatencySummaryNs& summary) {
    fmt::print(
        "[{}] samples={} min_ns={:.2f} p50_ns={:.2f} p99_ns={:.2f} p999_ns={:.2f} max_ns={:.2f}\n",
        transport,
        sample_count,
        summary.min_ns,
        summary.p50_ns,
        summary.p99_ns,
        summary.p999_ns,
        summary.max_ns
    );
}

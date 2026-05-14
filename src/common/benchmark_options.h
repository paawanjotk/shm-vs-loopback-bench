#pragma once

#include <string>

struct BenchmarkOptions {
    bool json_output = false;
    std::string run_id;
    std::string bench_mode = "unknown";
};

inline BenchmarkOptions parse_benchmark_options(int argc, char** argv, int start_index) {
    BenchmarkOptions o;
    for (int i = start_index; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--json") {
            o.json_output = true;
        } else if (a.rfind("--run-id=", 0) == 0) {
            o.run_id = a.substr(std::string("--run-id=").size());
        } else if (a.rfind("--bench-mode=", 0) == 0) {
            o.bench_mode = a.substr(std::string("--bench-mode=").size());
        }
    }
    if (o.run_id.empty()) {
        o.run_id = "local";
    }
    return o;
}

#include "publisher.h"

#include "../common/quote.h"
#include "../common/tsc_clock.h"
#include "xorshift.h"

#include <cstring>
#include <fcntl.h>
#include <fmt/core.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace {
constexpr const char* kShmName = "tryhard";
constexpr const char* kSocketPath = "/tmp/market.sock";
}

SharedMarketDataRegion* Publisher::create_shared_region(const char* name) {
    constexpr size_t kShmSize = sizeof(SharedMarketDataRegion);

    shm_unlink(name);
    int fd = shm_open(name, O_CREAT | O_RDWR, 0666);
    if (fd == -1) {
        perror("shm_open");
        return nullptr;
    }

    if (ftruncate(fd, kShmSize) == -1) {
        perror("ftruncate");
        close(fd);
        return nullptr;
    }

    void* ptr = mmap(nullptr, kShmSize, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (ptr == MAP_FAILED) {
        perror("mmap");
        return nullptr;
    }

    auto* region = new (ptr) SharedMarketDataRegion();
    region->ready.store(1, std::memory_order_release);
    fmt::print("[Publisher] Shared memory region ready at '{}'\n", name);
    return region;
}

Publisher::Publisher(PublisherMode mode) : mode_(mode) {}

void Publisher::run() {
    fmt::print("[Publisher] Starting publisher mode={}\n",
               mode_ == PublisherMode::SHM_ONLY ? "shm" : mode_ == PublisherMode::SOCKET_ONLY ? "socket" : "both");

    XorShift64 rng(123456);
    int64_t mid_cents = 285050;
    const int64_t spread_cents = 50;

    SharedMarketDataRegion* region = nullptr;
    if (mode_ != PublisherMode::SOCKET_ONLY) {
        region = create_shared_region(kShmName);
        if (!region) {
            fmt::print("[Publisher] ERROR: Failed to create shared memory region\n");
            return;
        }
    }

    int server_fd = -1;
    int client_fd = -1;
    if (mode_ != PublisherMode::SHM_ONLY) {
        server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (server_fd == -1) {
            perror("socket");
            return;
        }

        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        std::strncpy(addr.sun_path, kSocketPath, sizeof(addr.sun_path) - 1);
        unlink(addr.sun_path);

        if (bind(server_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == -1) {
            perror("bind");
            close(server_fd);
            return;
        }

        if (listen(server_fd, 1) == -1) {
            perror("listen");
            close(server_fd);
            return;
        }

        const int flags = fcntl(server_fd, F_GETFL, 0);
        fcntl(server_fd, F_SETFL, flags | O_NONBLOCK);
        fmt::print("[Publisher] Socket server listening on {}\n", kSocketPath);
    }

    uint64_t message_count = 0;
    while (true) {
        if (server_fd != -1 && client_fd == -1) {
            client_fd = accept(server_fd, nullptr, nullptr);
            if (client_fd != -1) {
                fmt::print("[Publisher] Socket subscriber connected\n");
            }
        }

        mid_cents += static_cast<int64_t>(rng.next_double(-5.0, 5.0));
        MarketMessageData msg{
            0,
            0,
            static_cast<uint32_t>(mid_cents + (spread_cents / 2)),
            static_cast<uint32_t>(mid_cents - (spread_cents / 2)),
            Instrument::RELIANCE
        };

        if (region) {
            msg.shm_timestamp = rdtsc_ordered();
            while (!region->queue.push(msg)) {
                __builtin_ia32_pause();
            }
        }

        if (server_fd != -1 && client_fd != -1) {
            msg.send_timestamp = rdtsc_ordered();
            ssize_t written = write(client_fd, &msg, sizeof(msg));
            if (written != static_cast<ssize_t>(sizeof(msg))) {
                close(client_fd);
                client_fd = -1;
            }
        }

        ++message_count;
        if (message_count % 1000000 == 0) {
            fmt::print("[Publisher] published={} mode={}\n",
                       message_count,
                       mode_ == PublisherMode::SHM_ONLY ? "shm" : mode_ == PublisherMode::SOCKET_ONLY ? "socket" : "both");
        }
    }
}
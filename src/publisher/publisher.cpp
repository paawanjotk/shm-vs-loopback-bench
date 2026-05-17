#include "publisher.h"

#include "../common/quote.h"
#include "../common/ringbuffer.h"
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
constexpr int kSocketBufBytes = static_cast<int>(kMarketQueueSize * sizeof(MarketMessageData));
constexpr uint32_t kAcceptPollMask = 0xFFFu;  // Poll accept() every 4096 iterations.
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
    uint64_t shm_dropped = 0;
    uint32_t accept_poll_counter = 0;
    while (true) {
        if (server_fd != -1 && client_fd == -1 &&
            ((accept_poll_counter++ & kAcceptPollMask) == 0)) {
            client_fd = accept(server_fd, nullptr, nullptr);
            if (client_fd != -1) {
                int sndbuf = kSocketBufBytes;
                if (setsockopt(client_fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf)) == -1) {
                    perror("setsockopt SO_SNDBUF");
                }
                int actual_sndbuf = 0;
                socklen_t optlen = sizeof(actual_sndbuf);
                getsockopt(client_fd, SOL_SOCKET, SO_SNDBUF, &actual_sndbuf, &optlen);
                fmt::print("[Publisher] Socket subscriber connected (SO_SNDBUF={} bytes)\n", actual_sndbuf);
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
            if (region->pause_publish.load(std::memory_order_acquire) == 0) {
                while (true) {
                    msg.shm_timestamp = rdtsc_ordered();
                    if (region->queue.push(msg)) {
                        break;
                    }
                    if (region->consumer_present.load(std::memory_order_acquire) != 1) {
                        ++shm_dropped;
                        break;
                    }
                    __builtin_ia32_pause();
                }
            } else {
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
        // if (message_count % 1000000 == 0) {
        //     const char* mode_label = mode_ == PublisherMode::SHM_ONLY ? "shm"
        //         : mode_ == PublisherMode::SOCKET_ONLY ? "socket" : "both";
        //     fmt::print("[Publisher] published={} mode={} shm_dropped={}\n",
        //                message_count, mode_label, shm_dropped);
        // }
    }
}
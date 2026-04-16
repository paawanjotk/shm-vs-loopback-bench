class XorShift64 {
    uint64_t state;

public:
    explicit XorShift64(uint64_t seed = 88172645463325252ull)
        : state(seed) {}

    inline uint64_t next() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        return state;
    }

    inline double next_double() {
        return (next() >> 11) * (1.0 / (1ULL << 53));
    }

    inline double next_double(double min, double max) {
        return min + next_double() * (max - min);
    }
};

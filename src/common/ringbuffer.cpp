#include "ringbuffer.h"
template <typename T, size_t size>
bool SPSCQueue<T, size>::push(const T& item){
    size_t curr_write = w.load(std::memory_order_relaxed);
    size_t next_write = (curr_write + 1) & (size - 1);
    
    if(next_write == r.load(std::memory_order_acquire)){
        return false;
    }

    buffer[curr_write] = item;

    w.store(next_write, std::memory_order_release);
    return true;
}

template<typename T, size_t size>
bool SPSCQueue<T, size>::pop(T& item){
    size_t curr_read = r.load(std::memory_order_relaxed);
    if(curr_read == w.load(std::memory_order_acquire)){
        return false;
    }
    item = buffer[curr_read];
    r.store((curr_read+1)&(size-1), std::memory_order_release);
    return true;
}

template<typename T, size_t size>
size_t SPSCQueue<T, size>::Size(){
    size_t curr_write = w.load(std::memory_order_acquire);
    size_t curr_read = r.load(std::memory_order_acquire);

    if(curr_write >= curr_read){
        return curr_write - curr_read;
    } else {
        return (size + curr_write - curr_read);
    }
}

template<typename T, size_t size>
bool SPSCQueue<T, size>::empty(){
    size_t curr_write = w.load(std::memory_order_acquire);
    size_t curr_read = r.load(std::memory_order_acquire);

    if(curr_write == curr_read){
        return true;
    }

    return false;
}

template class SPSCQueue<MarketMessageData, kMarketQueueSize>;
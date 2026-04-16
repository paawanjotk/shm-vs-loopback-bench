cmake_minimum_required(VERSION 3.15)

project(HFTProject VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -march=native -O3")

# Find required packages
find_package(fmt REQUIRED)
find_package(Boost REQUIRED COMPONENTS system)

add_executable(HFTApp 
    src/main.cpp 
    src/publisher/publisher.cpp 
    src/subscriber-s/subscriber.cpp 
    src/subscriber-l/subscriber.cpp
    src/common/ringbuffer.cpp
    src/common/tsc_clock.cpp
)

# Include directories
target_include_directories(HFTApp PRIVATE 
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${Boost_INCLUDE_DIRS}
)

# Link libraries
target_link_libraries(HFTApp PRIVATE
    fmt::fmt
    ${Boost_LIBRARIES}
    rt
    pthread
)
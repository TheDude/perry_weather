cmake_minimum_required(VERSION 3.16)
project(weather_sensor LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(SYSTEMD REQUIRED IMPORTED_TARGET libsystemd)

add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion)

# Protocol code has no OS dependencies so it can be unit tested anywhere.
add_library(weather_proto STATIC src/frame_parser.cpp)
target_include_directories(weather_proto PUBLIC src)

add_executable(weather-sensor src/main.cpp src/serial_port.cpp src/log_sink.cpp)
target_link_libraries(weather-sensor PRIVATE weather_proto PkgConfig::SYSTEMD)

include(GNUInstallDirs)
install(TARGETS weather-sensor RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES systemd/weather-sensor@.service DESTINATION lib/systemd/system)

include(CTest)
if(BUILD_TESTING)
  add_executable(test_frame_parser tests/test_frame_parser.cpp)
  target_link_libraries(test_frame_parser PRIVATE weather_proto)
  add_test(NAME frame_parser COMMAND test_frame_parser)
endif()

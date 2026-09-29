#pragma once

#include <string>

namespace weather {

// RAII wrapper around a raw, non-blocking 8N1 serial fd.
class SerialPort {
public:
    SerialPort() = default;
    ~SerialPort();
    SerialPort(const SerialPort&) = delete;
    SerialPort& operator=(const SerialPort&) = delete;

    // Opens and configures the tty. Returns false and sets `err` on failure.
    bool open(const std::string& path, unsigned baud, std::string& err);
    void close();

    int fd() const noexcept { return fd_; }
    bool is_open() const noexcept { return fd_ >= 0; }

private:
    int fd_ = -1;
};

} // namespace weather

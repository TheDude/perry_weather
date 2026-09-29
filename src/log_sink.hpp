#pragma once

#include "frame_parser.hpp"

#include <memory>
#include <string>

namespace weather {

// Where observations and diagnostics go. The journal sink attaches structured
// fields so records can be filtered/extracted with `journalctl -o json`.
class LogSink {
public:
    virtual ~LogSink() = default;
    virtual void observation(const Observation& obs) = 0;
    virtual void frame_error(const FrameErrorEvent& ev) = 0;
    // priority is a syslog level (LOG_ERR, LOG_WARNING, LOG_INFO, ...)
    virtual void message(int priority, const std::string& text) = 0;
};

std::unique_ptr<LogSink> make_journal_sink(const std::string& device);
std::unique_ptr<LogSink> make_stderr_sink();

std::string describe(const Observation& obs);
std::string hex(const std::uint8_t* data, std::size_t len);

} // namespace weather

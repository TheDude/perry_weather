#include "log_sink.hpp"

#include <cstdio>
#include <cstdlib>
#include <syslog.h>
#include <systemd/sd-journal.h>

namespace weather {

namespace {

// Fixed-point tenths -> "23.5" / "-0.5" without going through float.
std::string tenths(std::int16_t v)
{
    const int i = v;
    const int mag = std::abs(i);
    char buf[16];
    std::snprintf(buf, sizeof buf, "%s%d.%d", i < 0 ? "-" : "", mag / 10, mag % 10);
    return buf;
}

const char* error_name(FrameError e)
{
    switch (e) {
    case FrameError::BadCrc: return "crc_mismatch";
    case FrameError::UnknownType: return "unknown_ptype";
    }
    return "unknown";
}

class JournalSink final : public LogSink {
public:
    explicit JournalSink(std::string device) : device_(std::move(device)) {}

    void observation(const Observation& obs) override
    {
        const std::string msg = describe(obs);
        if (const auto* th = std::get_if<TempHumidity>(&obs)) {
            sd_journal_send("MESSAGE=%s", msg.c_str(),
                            "PRIORITY=%d", LOG_INFO,
                            "WEATHER_DEVICE=%s", device_.c_str(),
                            "WEATHER_PTYPE=temp_humidity",
                            "WEATHER_TEMP_C=%s", tenths(th->temp_dC).c_str(),
                            "WEATHER_HUMIDITY_PCT=%s", tenths(th->humidity_dPct).c_str(),
                            nullptr);
        } else if (const auto* wr = std::get_if<WindRain>(&obs)) {
            sd_journal_send("MESSAGE=%s", msg.c_str(),
                            "PRIORITY=%d", LOG_INFO,
                            "WEATHER_DEVICE=%s", device_.c_str(),
                            "WEATHER_PTYPE=wind_rain",
                            "WEATHER_WIND_MPS=%s", tenths(wr->wind_dMps).c_str(),
                            "WEATHER_RAIN_MM=%s", tenths(wr->rain_dMm).c_str(),
                            nullptr);
        }
    }

    void frame_error(const FrameErrorEvent& ev) override
    {
        const std::string raw = hex(ev.raw, kFrameSize);
        sd_journal_send("MESSAGE=Dropped frame (%s): %s", error_name(ev.error), raw.c_str(),
                        "PRIORITY=%d", LOG_WARNING,
                        "WEATHER_DEVICE=%s", device_.c_str(),
                        "WEATHER_FRAME_ERROR=%s", error_name(ev.error),
                        "WEATHER_RAW=%s", raw.c_str(),
                        nullptr);
    }

    void message(int priority, const std::string& text) override
    {
        sd_journal_send("MESSAGE=%s", text.c_str(),
                        "PRIORITY=%d", priority,
                        "WEATHER_DEVICE=%s", device_.c_str(),
                        nullptr);
    }

private:
    std::string device_;
};

// For running by hand / in CI where there is no journald.
class StderrSink final : public LogSink {
public:
    void observation(const Observation& obs) override
    {
        std::fprintf(stderr, "info: %s\n", describe(obs).c_str());
    }
    void frame_error(const FrameErrorEvent& ev) override
    {
        std::fprintf(stderr, "warning: Dropped frame (%s): %s\n", error_name(ev.error),
                     hex(ev.raw, kFrameSize).c_str());
    }
    void message(int priority, const std::string& text) override
    {
        const char* level = priority <= LOG_ERR       ? "error"
                            : priority == LOG_WARNING ? "warning"
                            : priority == LOG_NOTICE  ? "notice"
                                                      : "info";
        std::fprintf(stderr, "%s: %s\n", level, text.c_str());
    }
};

} // namespace

std::string describe(const Observation& obs)
{
    if (const auto* th = std::get_if<TempHumidity>(&obs)) {
        return "temperature=" + tenths(th->temp_dC) + "C humidity=" +
               tenths(th->humidity_dPct) + "%RH";
    }
    const auto& wr = std::get<WindRain>(obs);
    return "wind=" + tenths(wr.wind_dMps) + "m/s rain=" + tenths(wr.rain_dMm) + "mm";
}

std::string hex(const std::uint8_t* data, std::size_t len)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(len * 3);
    for (std::size_t i = 0; i < len; ++i) {
        if (i) out.push_back(' ');
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 0xF]);
    }
    return out;
}

std::unique_ptr<LogSink> make_journal_sink(const std::string& device)
{
    return std::make_unique<JournalSink>(device);
}

std::unique_ptr<LogSink> make_stderr_sink()
{
    return std::make_unique<StderrSink>();
}

} // namespace weather

#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <variant>
#include <vector>

namespace weather {

// Wire format (9 bytes, little-endian):
//   [0..1] sync 0xAA 0x55
//   [2]    ptype 0x01 = temp+humidity, 0x02 = wind+rain
//   [3..4] measurement 1, int16
//   [5..6] measurement 2, int16
//   [7..8] CRC-16/MODBUS over bytes 0..6
inline constexpr std::size_t kFrameSize = 9;
inline constexpr std::uint8_t kSync0 = 0xAA;
inline constexpr std::uint8_t kSync1 = 0x55;

enum class PacketType : std::uint8_t {
    TempHumidity = 0x01,
    WindRain = 0x02,
};

// Raw values are tenths of a unit (e.g. 235 == 23.5 degC), per the mock.
struct TempHumidity {
    std::int16_t temp_dC;      // 0.1 degC
    std::int16_t humidity_dPct; // 0.1 %RH
};

struct WindRain {
    std::int16_t wind_dMps; // 0.1 m/s
    std::int16_t rain_dMm;  // 0.1 mm accumulated since previous wind/rain packet
};

using Observation = std::variant<TempHumidity, WindRain>;

enum class FrameError {
    BadCrc,        // sync + known type found, CRC mismatch
    UnknownType,   // sync found, ptype not 0x01/0x02
};

struct FrameErrorEvent {
    FrameError error;
    std::uint8_t raw[kFrameSize];
};

struct ParserStats {
    std::uint64_t frames_ok = 0;
    std::uint64_t crc_errors = 0;
    std::uint64_t unknown_type = 0;
    std::uint64_t bytes_discarded = 0; // bytes skipped while hunting for sync
    std::uint64_t truncated = 0;       // partial frames dropped by reset()
};

// Streaming, byte-oriented frame decoder. Feed it whatever read() returned;
// it calls the handlers for each complete frame. It never assumes a read()
// boundary coincides with a frame boundary.
class FrameParser {
public:
    using ObservationHandler = std::function<void(const Observation&)>;
    using ErrorHandler = std::function<void(const FrameErrorEvent&)>;

    FrameParser(ObservationHandler on_obs, ErrorHandler on_err);

    void feed(const std::uint8_t* data, std::size_t len);

    // Drop any partially received frame (e.g. after an inter-frame gap or a
    // reconnect). Counted as a truncated frame if bytes were pending.
    void reset();

    bool has_pending() const noexcept { return !buf_.empty(); }
    const ParserStats& stats() const noexcept { return stats_; }

private:
    void process();

    ObservationHandler on_obs_;
    ErrorHandler on_err_;
    std::vector<std::uint8_t> buf_;
    ParserStats stats_;
};

} // namespace weather

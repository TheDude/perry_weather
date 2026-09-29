#include "frame_parser.hpp"

#include "crc16.hpp"

#include <algorithm>
#include <cstring>
#include <utility>

namespace weather {

namespace {

std::int16_t read_i16_le(const std::uint8_t* p) noexcept
{
    return static_cast<std::int16_t>(static_cast<std::uint16_t>(p[0]) |
                                     (static_cast<std::uint16_t>(p[1]) << 8));
}

std::uint16_t read_u16_le(const std::uint8_t* p) noexcept
{
    return static_cast<std::uint16_t>(p[0] | (p[1] << 8));
}

} // namespace

FrameParser::FrameParser(ObservationHandler on_obs, ErrorHandler on_err)
    : on_obs_(std::move(on_obs)), on_err_(std::move(on_err))
{
    buf_.reserve(64);
}

void FrameParser::feed(const std::uint8_t* data, std::size_t len)
{
    buf_.insert(buf_.end(), data, data + len);
    process();
}

void FrameParser::reset()
{
    if (!buf_.empty()) {
        ++stats_.truncated;
        buf_.clear();
    }
}

void FrameParser::process()
{
    for (;;) {
        // 1. Hunt for the sync word. Anything before it is noise.
        std::size_t pos = 0;
        while (pos + 1 < buf_.size() && !(buf_[pos] == kSync0 && buf_[pos + 1] == kSync1)) {
            ++pos;
        }
        if (pos + 1 >= buf_.size()) {
            // No full sync word. Keep a trailing 0xAA: it may be the first half.
            const bool keep_last = !buf_.empty() && buf_.back() == kSync0;
            const std::size_t drop = buf_.size() - (keep_last ? 1 : 0);
            stats_.bytes_discarded += drop;
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(drop));
            return;
        }
        if (pos > 0) {
            stats_.bytes_discarded += pos;
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(pos));
        }

        // 2. Wait for a whole frame.
        if (buf_.size() < kFrameSize) {
            return;
        }

        const std::uint8_t* f = buf_.data();
        FrameErrorEvent ev{};
        std::memcpy(ev.raw, f, kFrameSize);

        // 3. Validate. On any failure, skip only the first sync byte and
        //    re-hunt: a false sync inside noise must not swallow a real frame
        //    that starts a few bytes later.
        const std::uint8_t ptype = f[2];
        const bool known = ptype == static_cast<std::uint8_t>(PacketType::TempHumidity) ||
                           ptype == static_cast<std::uint8_t>(PacketType::WindRain);
        const bool crc_ok = crc16_modbus(f, 7) == read_u16_le(f + 7);

        if (!crc_ok || !known) {
            // Report CRC failure in preference to unknown type: a bit flip in
            // the ptype byte is corruption, not a new packet kind.
            ev.error = crc_ok ? FrameError::UnknownType : FrameError::BadCrc;
            if (crc_ok) {
                ++stats_.unknown_type;
            } else {
                ++stats_.crc_errors;
            }
            ++stats_.bytes_discarded;
            buf_.erase(buf_.begin());
            if (on_err_) on_err_(ev);
            continue;
        }

        const std::int16_t m1 = read_i16_le(f + 3);
        const std::int16_t m2 = read_i16_le(f + 5);
        buf_.erase(buf_.begin(), buf_.begin() + kFrameSize);
        ++stats_.frames_ok;

        if (on_obs_) {
            if (ptype == static_cast<std::uint8_t>(PacketType::TempHumidity)) {
                on_obs_(TempHumidity{m1, m2});
            } else {
                on_obs_(WindRain{m1, m2});
            }
        }
    }
}

} // namespace weather

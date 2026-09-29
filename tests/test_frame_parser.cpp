// Minimal self-contained tests (no framework dependency) run via ctest.

#include "crc16.hpp"
#include "frame_parser.hpp"

#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace weather;

static int g_failures = 0;
#define CHECK(cond)                                                                 \
    do {                                                                            \
        if (!(cond)) {                                                              \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                                           \
        }                                                                           \
    } while (0)

namespace {

std::vector<std::uint8_t> make_frame(std::uint8_t ptype, std::int16_t m1, std::int16_t m2)
{
    std::vector<std::uint8_t> f = {
        kSync0, kSync1, ptype,
        static_cast<std::uint8_t>(m1 & 0xFF), static_cast<std::uint8_t>((m1 >> 8) & 0xFF),
        static_cast<std::uint8_t>(m2 & 0xFF), static_cast<std::uint8_t>((m2 >> 8) & 0xFF),
    };
    const std::uint16_t crc = crc16_modbus(f.data(), f.size());
    f.push_back(static_cast<std::uint8_t>(crc & 0xFF));
    f.push_back(static_cast<std::uint8_t>(crc >> 8));
    return f;
}

struct Harness {
    std::vector<Observation> obs;
    std::vector<FrameErrorEvent> errs;
    FrameParser parser{[this](const Observation& o) { obs.push_back(o); },
                       [this](const FrameErrorEvent& e) { errs.push_back(e); }};

    void feed(const std::vector<std::uint8_t>& v) { parser.feed(v.data(), v.size()); }
};

void append(std::vector<std::uint8_t>& a, const std::vector<std::uint8_t>& b)
{
    a.insert(a.end(), b.begin(), b.end());
}

void test_crc_check_value()
{
    const std::uint8_t s[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(crc16_modbus(s, sizeof s) == 0x4B37);
}

void test_known_frame_from_mock()
{
    // Golden frames from the reference mock (independent CRC implementation):
    //   python3 -c "import mock; print(mock.build_a_temp_humid(220,550).hex(' '))"
    //   python3 -c "import mock; print(mock.build_a_wind_rain(30,-1).hex(' '))"
    Harness h;
    h.feed({0xaa, 0x55, 0x01, 0xdc, 0x00, 0x26, 0x02, 0x80, 0xfe});
    h.feed({0xaa, 0x55, 0x02, 0x1e, 0x00, 0xff, 0xff, 0x63, 0x57});
    CHECK(h.errs.empty());
    CHECK(h.obs.size() == 2);
    const auto* th = std::get_if<TempHumidity>(&h.obs.at(0));
    CHECK(th && th->temp_dC == 220 && th->humidity_dPct == 550);
    const auto* wr = std::get_if<WindRain>(&h.obs.at(1));
    CHECK(wr && wr->wind_dMps == 30 && wr->rain_dMm == -1);
}

void test_single_frame_each_type()
{
    Harness h;
    h.feed(make_frame(0x01, 235, 550));
    h.feed(make_frame(0x02, 42, 7));
    CHECK(h.obs.size() == 2);
    CHECK(h.errs.empty());
    const auto* th = std::get_if<TempHumidity>(&h.obs.at(0));
    CHECK(th && th->temp_dC == 235 && th->humidity_dPct == 550);
    const auto* wr = std::get_if<WindRain>(&h.obs.at(1));
    CHECK(wr && wr->wind_dMps == 42 && wr->rain_dMm == 7);
}

void test_negative_values()
{
    Harness h;
    h.feed(make_frame(0x01, -50, 200));
    const auto* th = std::get_if<TempHumidity>(&h.obs.at(0));
    CHECK(th && th->temp_dC == -50);
}

void test_byte_at_a_time()
{
    Harness h;
    std::vector<std::uint8_t> stream;
    append(stream, make_frame(0x01, 1, 2));
    append(stream, make_frame(0x02, 3, 4));
    for (auto b : stream) h.parser.feed(&b, 1);
    CHECK(h.obs.size() == 2);
    CHECK(h.parser.stats().bytes_discarded == 0);
}

void test_leading_garbage_and_split_sync()
{
    Harness h;
    std::vector<std::uint8_t> stream = {0x00, 0x13, 0xAA, 0xAA, 0x01};
    append(stream, make_frame(0x01, 10, 20));
    // Split right between 0xAA and 0x55 of the real sync.
    const std::size_t split = 6; // index of 0x55 in stream
    h.parser.feed(stream.data(), split);
    h.parser.feed(stream.data() + split, stream.size() - split);
    CHECK(h.obs.size() == 1);
    CHECK(h.errs.empty());
    CHECK(h.parser.stats().bytes_discarded == 5);
}

void test_crc_error_then_recover()
{
    Harness h;
    auto bad = make_frame(0x01, 100, 200);
    bad[4] ^= 0x10; // single bit flip in payload, like the mock does
    std::vector<std::uint8_t> stream = bad;
    append(stream, make_frame(0x02, 5, 6));
    h.feed(stream);
    CHECK(h.errs.size() == 1);
    CHECK(h.errs.at(0).error == FrameError::BadCrc);
    CHECK(h.obs.size() == 1);
    CHECK(std::holds_alternative<WindRain>(h.obs.at(0)));
    CHECK(h.parser.stats().crc_errors == 1);
}

void test_flip_in_ptype_is_crc_error()
{
    Harness h;
    auto bad = make_frame(0x01, 100, 200);
    bad[2] ^= 0x04; // ptype 0x05
    h.feed(bad);
    CHECK(h.errs.size() == 1 && h.errs.at(0).error == FrameError::BadCrc);
}

void test_valid_crc_unknown_type()
{
    Harness h;
    h.feed(make_frame(0x07, 1, 2));
    CHECK(h.errs.size() == 1 && h.errs.at(0).error == FrameError::UnknownType);
    CHECK(h.obs.empty());
}

void test_false_sync_inside_noise()
{
    // A false "AA 55" whose 9-byte window overlaps a real frame must not
    // swallow it.
    Harness h;
    std::vector<std::uint8_t> stream = {0xAA, 0x55, 0x01, 0x00};
    append(stream, make_frame(0x01, 11, 22));
    h.feed(stream);
    CHECK(h.obs.size() == 1);
    const auto* th = std::get_if<TempHumidity>(&h.obs.at(0));
    CHECK(th && th->temp_dC == 11 && th->humidity_dPct == 22);
}

void test_reset_drops_partial()
{
    Harness h;
    auto f = make_frame(0x01, 1, 2);
    h.parser.feed(f.data(), 5);
    CHECK(h.parser.has_pending());
    h.parser.reset();
    CHECK(!h.parser.has_pending());
    CHECK(h.parser.stats().truncated == 1);
    h.feed(make_frame(0x02, 3, 4));
    CHECK(h.obs.size() == 1 && h.errs.empty());
}

void test_every_single_bit_flip_detected()
{
    // CRC-16 detects all single-bit errors; confirm the parser never emits a
    // corrupted observation for any single flip past the sync word.
    const auto good = make_frame(0x01, 123, 456);
    for (std::size_t byte = 2; byte < kFrameSize; ++byte) {
        for (int bit = 0; bit < 8; ++bit) {
            Harness h;
            auto bad = good;
            bad[byte] ^= static_cast<std::uint8_t>(1U << bit);
            h.feed(bad);
            CHECK(h.obs.empty());
        }
    }
}

} // namespace

int main()
{
    test_crc_check_value();
    test_known_frame_from_mock();
    test_single_frame_each_type();
    test_negative_values();
    test_byte_at_a_time();
    test_leading_garbage_and_split_sync();
    test_crc_error_then_recover();
    test_flip_in_ptype_is_crc_error();
    test_valid_crc_unknown_type();
    test_false_sync_inside_noise();
    test_reset_drops_partial();
    test_every_single_bit_flip_detected();

    if (g_failures) {
        std::fprintf(stderr, "%d check(s) failed\n", g_failures);
        return EXIT_FAILURE;
    }
    std::puts("all tests passed");
    return EXIT_SUCCESS;
}

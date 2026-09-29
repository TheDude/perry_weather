import struct
import unittest

from weather_sensor.frame_parser import (
    FRAME_SIZE, FrameError, FrameParser, TempHumidity, WindRain, crc16_modbus,
)
from weather_sensor.sinks import tenths


def make_frame(ptype: int, m1: int, m2: int) -> bytes:
    body = b"\xaa\x55" + struct.pack("<Bhh", ptype, m1, m2)
    return body + struct.pack("<H", crc16_modbus(body))


class Harness:
    def __init__(self):
        self.obs = []
        self.errs = []
        self.parser = FrameParser(self.obs.append, self.errs.append)

    def feed(self, data: bytes):
        self.parser.feed(data)


class CrcTest(unittest.TestCase):
    def test_check_value(self):
        self.assertEqual(crc16_modbus(b"123456789"), 0x4B37)


class FrameParserTest(unittest.TestCase):
    def test_golden_frames_from_mock(self):
        # Produced by the reference mock (independent CRC implementation):
        #   python3 -c "import mock; print(mock.build_a_temp_humid(220,550).hex(' '))"
        #   python3 -c "import mock; print(mock.build_a_wind_rain(30,-1).hex(' '))"
        h = Harness()
        h.feed(bytes.fromhex("aa 55 01 dc 00 26 02 80 fe"))
        h.feed(bytes.fromhex("aa 55 02 1e 00 ff ff 63 57"))
        self.assertEqual(h.errs, [])
        self.assertEqual(h.obs, [TempHumidity(220, 550), WindRain(30, -1)])

    def test_each_type_and_negative(self):
        h = Harness()
        h.feed(make_frame(0x01, -50, 200) + make_frame(0x02, 42, 7))
        self.assertEqual(h.obs, [TempHumidity(-50, 200), WindRain(42, 7)])

    def test_byte_at_a_time(self):
        h = Harness()
        for b in make_frame(0x01, 1, 2) + make_frame(0x02, 3, 4):
            h.feed(bytes([b]))
        self.assertEqual(len(h.obs), 2)
        self.assertEqual(h.parser.stats.bytes_discarded, 0)

    def test_leading_garbage_and_split_sync(self):
        h = Harness()
        stream = b"\x00\x13\xaa\xaa\x01" + make_frame(0x01, 10, 20)
        split = 6  # between 0xAA and 0x55 of the real sync
        h.feed(stream[:split])
        h.feed(stream[split:])
        self.assertEqual(h.obs, [TempHumidity(10, 20)])
        self.assertEqual(h.errs, [])
        self.assertEqual(h.parser.stats.bytes_discarded, 5)

    def test_crc_error_then_recover(self):
        h = Harness()
        bad = bytearray(make_frame(0x01, 100, 200))
        bad[4] ^= 0x10  # single bit flip, like the mock does
        h.feed(bytes(bad) + make_frame(0x02, 5, 6))
        self.assertEqual([e.error for e in h.errs], [FrameError.BAD_CRC])
        self.assertEqual(h.obs, [WindRain(5, 6)])
        self.assertEqual(h.parser.stats.crc_errors, 1)

    def test_flip_in_ptype_is_crc_error(self):
        h = Harness()
        bad = bytearray(make_frame(0x01, 100, 200))
        bad[2] ^= 0x04
        h.feed(bytes(bad))
        self.assertEqual([e.error for e in h.errs], [FrameError.BAD_CRC])

    def test_valid_crc_unknown_type(self):
        h = Harness()
        h.feed(make_frame(0x07, 1, 2))
        self.assertEqual([e.error for e in h.errs], [FrameError.UNKNOWN_TYPE])
        self.assertEqual(h.obs, [])

    def test_false_sync_inside_noise(self):
        # A false "AA 55" whose window overlaps a real frame must not swallow it.
        h = Harness()
        h.feed(b"\xaa\x55\x01\x00" + make_frame(0x01, 11, 22))
        self.assertEqual(h.obs, [TempHumidity(11, 22)])

    def test_reset_drops_partial(self):
        h = Harness()
        h.feed(make_frame(0x01, 1, 2)[:5])
        self.assertTrue(h.parser.has_pending)
        h.parser.reset()
        self.assertFalse(h.parser.has_pending)
        self.assertEqual(h.parser.stats.truncated, 1)
        h.feed(make_frame(0x02, 3, 4))
        self.assertEqual(h.obs, [WindRain(3, 4)])
        self.assertEqual(h.errs, [])

    def test_every_single_bit_flip_detected(self):
        good = make_frame(0x01, 123, 456)
        for byte in range(2, FRAME_SIZE):
            for bit in range(8):
                h = Harness()
                bad = bytearray(good)
                bad[byte] ^= 1 << bit
                h.feed(bytes(bad))
                self.assertEqual(h.obs, [], f"byte {byte} bit {bit}")


class FormatTest(unittest.TestCase):
    def test_tenths(self):
        self.assertEqual(tenths(235), "23.5")
        self.assertEqual(tenths(-5), "-0.5")
        self.assertEqual(tenths(-50), "-5.0")
        self.assertEqual(tenths(0), "0.0")
        self.assertEqual(tenths(-32768), "-3276.8")


if __name__ == "__main__":
    unittest.main()

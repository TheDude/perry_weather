import struct
import unittest

from frame_parser import FRAME_SIZE, BadFrame, FrameParser, TempHumidity, WindRain, crc16_modbus


def make_frame(ptype: int, m1: int, m2: int) -> bytes:
    body = b"\xaa\x55" + struct.pack("<Bhh", ptype, m1, m2)
    return body + struct.pack("<H", crc16_modbus(body))


def flip(frame: bytes, byte: int, bit: int) -> bytes:
    b = bytearray(frame)
    b[byte] ^= 1 << bit
    return bytes(b)


class CrcTest(unittest.TestCase):
    def test_check_value(self):
        self.assertEqual(crc16_modbus(b"123456789"), 0x4B37)


class FrameParserTest(unittest.TestCase):
    def test_golden_frames_from_mock(self):
        # From the reference mock (independent CRC implementation):
        #   mock.build_a_temp_humid(220, 550), mock.build_a_wind_rain(30, -1)
        p = FrameParser()
        self.assertEqual(p.feed(bytes.fromhex("aa 55 01 dc 00 26 02 80 fe")), [TempHumidity(220, 550)])
        self.assertEqual(p.feed(bytes.fromhex("aa 55 02 1e 00 ff ff 63 57")), [WindRain(30, -1)])

    def test_two_frames_one_read(self):
        p = FrameParser()
        out = p.feed(make_frame(0x01, -50, 200) + make_frame(0x02, 42, 7))
        self.assertEqual(out, [TempHumidity(-50, 200), WindRain(42, 7)])

    def test_byte_at_a_time(self):
        p = FrameParser()
        out = []
        for b in make_frame(0x01, 1, 2) + make_frame(0x02, 3, 4):
            out += p.feed(bytes([b]))
        self.assertEqual(out, [TempHumidity(1, 2), WindRain(3, 4)])

    def test_leading_garbage_and_split_sync(self):
        p = FrameParser()
        stream = b"\x00\x13\xaa\xaa\x01" + make_frame(0x01, 10, 20)
        split = 6  # between the 0xAA and 0x55 of the real sync
        out = p.feed(stream[:split]) + p.feed(stream[split:])
        self.assertEqual(out, [TempHumidity(10, 20)])

    def test_crc_error_then_recover(self):
        p = FrameParser()
        bad = flip(make_frame(0x01, 100, 200), 4, 4)
        out = p.feed(bad + make_frame(0x02, 5, 6))
        self.assertEqual(out, [BadFrame("crc_mismatch", bad), WindRain(5, 6)])

    def test_valid_crc_unknown_type(self):
        p = FrameParser()
        frame = make_frame(0x07, 1, 2)
        self.assertEqual(p.feed(frame), [BadFrame("unknown_ptype", frame)])

    def test_false_sync_inside_noise(self):
        # A false "AA 55" whose 9-byte window overlaps a real frame must not swallow it.
        p = FrameParser()
        out = p.feed(b"\xaa\x55\x01\x00" + make_frame(0x01, 11, 22))
        self.assertEqual(out[-1], TempHumidity(11, 22))

    def test_every_single_bit_flip_detected(self):
        good = make_frame(0x01, 123, 456)
        for byte in range(2, FRAME_SIZE):
            for bit in range(8):
                out = FrameParser().feed(flip(good, byte, bit))
                self.assertTrue(all(isinstance(r, BadFrame) for r in out), f"byte {byte} bit {bit}")


if __name__ == "__main__":
    unittest.main()

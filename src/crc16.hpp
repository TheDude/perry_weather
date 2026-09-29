#pragma once

#include <cstddef>
#include <cstdint>

namespace weather {

// CRC-16/MODBUS: reflected poly 0x8005 (0xA001), init 0xFFFF, no xorout.
// Check value for "123456789" is 0x4B37 (verified in tests).
constexpr std::uint16_t crc16_modbus(const std::uint8_t* data, std::size_t len) noexcept
{
    std::uint16_t crc = 0xFFFF;
    for (std::size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 1U) ? static_cast<std::uint16_t>((crc >> 1) ^ 0xA001U)
                             : static_cast<std::uint16_t>(crc >> 1);
        }
    }
    return crc;
}

} // namespace weather

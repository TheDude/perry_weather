import argparse
from pathlib import Path
import serial

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pty', type=Path, help='path to pty')
    args = parser.parse_args()
    if not args.pty.exists():
        parser.error(f'File not found {args.pty}')

    with serial.Serial(str(args.pty), baudrate=9600, bytesize=8, parity='N', stopbits=1, timeout=None) as ser:
        buffer = bytearray()
        while True:
            buffer += ser.read_all() #really not the right way to do this.
            if len(buffer) >= 9:
                syncByte = buffer[:2]
                if syncByte == b'\xAA\x55':
                    expected_crc = buffer[7:9]
                    ptype = buffer[2]
                    if crc16_modbus(buffer[:7]) == int.from_bytes(expected_crc, byteorder='little') and ptype in (0x01, 0x02):
                        if ptype == 0x01:
                            temp = buffer[3:5]
                            humidity = buffer[5:7]
                            print(f"Temperature: {int.from_bytes(temp, byteorder='little', signed=True)}")
                            print(f"Humiditiy: {int.from_bytes(humidity, byteorder='little', signed=True)}")
                        elif ptype == 0x02:
                            wind = buffer[3:5]
                            rain = buffer[5:7]
                            print(f"Wind: {int.from_bytes(wind, byteorder='little', signed=True)}")
                            print(f"Rain: {int.from_bytes(rain, byteorder='little', signed=True)}")
                        del buffer[:9]
                    else:
                        del buffer[0]
                else:
                    del buffer[0]

def crc16_modbus(data: bytearray) -> int:
    """
    Compute CRC-16/MODBUS over `data`.
    Poly 0xA001 (reflected), init 0xFFFF, no final XOR.
    Returns the CRC as an int (0x0000-0xFFFF).

    This function written by Claude
    """
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

if __name__ == "__main__":
    main()
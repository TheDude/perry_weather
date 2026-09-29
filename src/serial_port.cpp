#include "serial_port.hpp"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

namespace weather {

namespace {

bool baud_to_speed(unsigned baud, speed_t& out)
{
    switch (baud) {
    case 1200: out = B1200; return true;
    case 2400: out = B2400; return true;
    case 4800: out = B4800; return true;
    case 9600: out = B9600; return true;
    case 19200: out = B19200; return true;
    case 38400: out = B38400; return true;
    case 57600: out = B57600; return true;
    case 115200: out = B115200; return true;
    default: return false;
    }
}

std::string errno_str(const char* what)
{
    return std::string(what) + ": " + std::strerror(errno);
}

} // namespace

SerialPort::~SerialPort() { close(); }

void SerialPort::close()
{
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}

bool SerialPort::open(const std::string& path, unsigned baud, std::string& err)
{
    close();

    speed_t speed{};
    if (!baud_to_speed(baud, speed)) {
        err = "unsupported baud rate " + std::to_string(baud);
        return false;
    }

    // O_NOCTTY: never become our controlling terminal.
    // O_NONBLOCK: don't hang in open() waiting for DCD; reads are poll()-driven.
    const int fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        err = errno_str("open");
        return false;
    }

    // Exclusive access: a second reader on the same UART would silently steal
    // bytes from us and look like line noise.
    if (::ioctl(fd, TIOCEXCL) < 0) {
        err = errno_str("ioctl TIOCEXCL");
        ::close(fd);
        return false;
    }

    termios tio{};
    if (::tcgetattr(fd, &tio) < 0) {
        err = errno_str("tcgetattr (not a tty?)");
        ::close(fd);
        return false;
    }

    // Raw mode: no line discipline, no echo, no CR/LF or XON/XOFF translation.
    // Without this, 0x0D/0x11/0x13 bytes in a binary frame get mangled.
    ::cfmakeraw(&tio);

    // 8N1, receiver on, ignore modem control lines, no HW flow control.
    tio.c_cflag &= ~static_cast<tcflag_t>(CSIZE | PARENB | CSTOPB | CRTSCTS);
    tio.c_cflag |= CS8 | CREAD | CLOCAL;

    // poll() does the waiting; with O_NONBLOCK these don't make read() block.
    // VMIN must be >= 1: with VMIN=0/VTIME=0, n_tty returns 0 (not EAGAIN)
    // on an empty buffer, which is indistinguishable from EOF/hangup.
    tio.c_cc[VMIN] = 1;
    tio.c_cc[VTIME] = 0;

    ::cfsetispeed(&tio, speed);
    ::cfsetospeed(&tio, speed);

    if (::tcsetattr(fd, TCSANOW, &tio) < 0) {
        err = errno_str("tcsetattr");
        ::close(fd);
        return false;
    }

    // Discard anything buffered before we configured the port.
    ::tcflush(fd, TCIFLUSH);

    fd_ = fd;
    return true;
}

} // namespace weather

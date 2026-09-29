// weather-sensor: read observations from an RS485 weather sensor on a serial
// tty and log them to the systemd journal.

#include "frame_parser.hpp"
#include "log_sink.hpp"
#include "serial_port.hpp"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <getopt.h>
#include <poll.h>
#include <string>
#include <sys/signalfd.h>
#include <syslog.h>
#include <systemd/sd-daemon.h>
#include <unistd.h>

using namespace weather;
using Clock = std::chrono::steady_clock;
using std::chrono::milliseconds;

namespace {

struct Config {
    std::string device;
    unsigned baud = 9600;
    bool use_stderr = false;
    milliseconds stale_timeout{10'000}; // sensor publishes every 2 s
    milliseconds frame_gap{100};        // a 9-byte frame takes ~9.4 ms at 9600
};

void usage(const char* argv0)
{
    std::fprintf(stderr,
                 "Usage: %s [options] [DEVICE]\n"
                 "\n"
                 "  DEVICE                  serial tty (or $WEATHER_TTY)\n"
                 "  -b, --baud N            baud rate (default 9600)\n"
                 "  -s, --stale-timeout MS  warn if no valid frame for MS (default 10000)\n"
                 "  -g, --frame-gap MS      drop a partial frame after MS of silence (default 100)\n"
                 "  -e, --stderr            log to stderr instead of the journal\n"
                 "  -h, --help\n",
                 argv0);
}

bool parse_uint(const char* s, unsigned long& out)
{
    char* end = nullptr;
    errno = 0;
    out = std::strtoul(s, &end, 10);
    return errno == 0 && end != s && *end == '\0';
}

bool parse_args(int argc, char** argv, Config& cfg)
{
    static const option longopts[] = {
        {"baud", required_argument, nullptr, 'b'},
        {"stale-timeout", required_argument, nullptr, 's'},
        {"frame-gap", required_argument, nullptr, 'g'},
        {"stderr", no_argument, nullptr, 'e'},
        {"help", no_argument, nullptr, 'h'},
        {nullptr, 0, nullptr, 0},
    };
    int c;
    unsigned long v;
    while ((c = getopt_long(argc, argv, "b:s:g:eh", longopts, nullptr)) != -1) {
        switch (c) {
        case 'b':
            if (!parse_uint(optarg, v)) return false;
            cfg.baud = static_cast<unsigned>(v);
            break;
        case 's':
            if (!parse_uint(optarg, v) || v == 0) return false;
            cfg.stale_timeout = milliseconds(v);
            break;
        case 'g':
            if (!parse_uint(optarg, v) || v == 0) return false;
            cfg.frame_gap = milliseconds(v);
            break;
        case 'e': cfg.use_stderr = true; break;
        case 'h': usage(argv[0]); std::exit(0);
        default: return false;
        }
    }
    if (optind < argc) {
        cfg.device = argv[optind++];
    } else if (const char* env = std::getenv("WEATHER_TTY"); env && *env) {
        cfg.device = env;
    }
    return !cfg.device.empty() && optind == argc;
}

// Block the signals we care about and receive them via an fd, so shutdown is
// just another poll() event -- no async-signal-safety concerns.
int make_signalfd()
{
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGINT);
    sigaddset(&mask, SIGTERM);
    sigaddset(&mask, SIGUSR1);
    if (sigprocmask(SIG_BLOCK, &mask, nullptr) < 0) return -1;
    return signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
}

std::string stats_line(const ParserStats& s)
{
    return "stats: frames_ok=" + std::to_string(s.frames_ok) +
           " crc_errors=" + std::to_string(s.crc_errors) +
           " unknown_type=" + std::to_string(s.unknown_type) +
           " truncated=" + std::to_string(s.truncated) +
           " bytes_discarded=" + std::to_string(s.bytes_discarded);
}

class Service {
public:
    Service(Config cfg, std::unique_ptr<LogSink> sink, int sigfd)
        : cfg_(std::move(cfg)), sink_(std::move(sink)), sigfd_(sigfd),
          parser_([this](const Observation& o) { on_observation(o); },
                  [this](const FrameErrorEvent& e) { sink_->frame_error(e); })
    {
        std::uint64_t wd_usec = 0;
        if (sd_watchdog_enabled(0, &wd_usec) > 0) {
            watchdog_interval_ = std::chrono::duration_cast<milliseconds>(
                std::chrono::microseconds(wd_usec / 2));
        }
    }

    int run()
    {
        sd_notify(0, "READY=1\nSTATUS=Starting");
        milliseconds backoff{500};
        bool reported_open_failure = false;

        while (running_) {
            std::string err;
            if (!port_.open(cfg_.device, cfg_.baud, err)) {
                // Log the first failure of an outage loudly, then stay quiet
                // so an unplugged sensor doesn't flood the journal.
                if (!reported_open_failure) {
                    sink_->message(LOG_ERR, "Cannot open " + cfg_.device + ": " + err +
                                                " (retrying)");
                    sd_notifyf(0, "STATUS=Waiting for %s", cfg_.device.c_str());
                    reported_open_failure = true;
                }
                wait_for_signal(backoff);
                backoff = std::min(backoff * 2, milliseconds{30'000});
                continue;
            }

            reported_open_failure = false;
            backoff = milliseconds{500};
            sink_->message(LOG_NOTICE, "Opened " + cfg_.device + " at " +
                                           std::to_string(cfg_.baud) + " 8N1");
            sd_notifyf(0, "STATUS=Reading %s", cfg_.device.c_str());

            read_loop();
            port_.close();
            parser_.reset();
        }

        sd_notify(0, "STOPPING=1");
        sink_->message(LOG_NOTICE, "Shutting down; " + stats_line(parser_.stats()));
        return 0;
    }

private:
    void on_observation(const Observation& obs)
    {
        last_good_ = Clock::now();
        if (stale_) {
            stale_ = false;
            sink_->message(LOG_NOTICE, "Sensor data resumed");
        }
        sink_->observation(obs);
    }

    // Returns when the port needs reopening or a shutdown was requested.
    void read_loop()
    {
        last_good_ = last_rx_ = Clock::now();
        stale_ = false;

        while (running_) {
            pollfd fds[2] = {
                {port_.fd(), POLLIN, 0},
                {sigfd_, POLLIN, 0},
            };
            const int n = ::poll(fds, 2, 50);
            if (n < 0 && errno != EINTR) {
                sink_->message(LOG_ERR, std::string("poll: ") + std::strerror(errno));
                return;
            }
            const auto now = Clock::now();
            kick_watchdog(now);

            if (fds[1].revents & POLLIN) handle_signals();

            if (fds[0].revents & POLLIN) {
                if (!drain_port()) return;
                last_rx_ = now;
            } else if (fds[0].revents & (POLLHUP | POLLERR | POLLNVAL)) {
                sink_->message(LOG_ERR, "Lost " + cfg_.device + " (hangup/error); reopening");
                return;
            }

            // Bytes stopped arriving mid-frame: the rest isn't coming.
            if (parser_.has_pending() && now - last_rx_ > cfg_.frame_gap) {
                parser_.reset();
                sink_->message(LOG_WARNING, "Dropped partial frame after inter-frame gap");
            }

            if (!stale_ && now - last_good_ > cfg_.stale_timeout) {
                stale_ = true;
                sink_->message(LOG_WARNING,
                               "No valid observation for " +
                                   std::to_string(cfg_.stale_timeout.count()) + " ms");
            }
        }
    }

    // Read everything available. Returns false if the port went away.
    bool drain_port()
    {
        std::uint8_t buf[256];
        for (;;) {
            const ssize_t r = ::read(port_.fd(), buf, sizeof buf);
            if (r > 0) {
                parser_.feed(buf, static_cast<std::size_t>(r));
                continue;
            }
            if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return true;
            if (r < 0 && errno == EINTR) continue;
            sink_->message(LOG_ERR, "Read from " + cfg_.device + " failed: " +
                                        (r == 0 ? "EOF" : std::strerror(errno)) +
                                        "; reopening");
            return false;
        }
    }

    void handle_signals()
    {
        signalfd_siginfo si;
        while (::read(sigfd_, &si, sizeof si) == sizeof si) {
            if (si.ssi_signo == SIGUSR1) {
                sink_->message(LOG_INFO, stats_line(parser_.stats()));
            } else {
                running_ = false;
            }
        }
    }

    // Sleep up to `d`, waking early on a signal.
    void wait_for_signal(milliseconds d)
    {
        const auto deadline = Clock::now() + d;
        while (running_) {
            const auto now = Clock::now();
            if (now >= deadline) return;
            kick_watchdog(now);
            auto left = std::chrono::duration_cast<milliseconds>(deadline - now);
            if (watchdog_interval_.count() > 0) left = std::min(left, watchdog_interval_);
            pollfd pfd{sigfd_, POLLIN, 0};
            if (::poll(&pfd, 1, static_cast<int>(left.count())) > 0) handle_signals();
        }
    }

    void kick_watchdog(Clock::time_point now)
    {
        if (watchdog_interval_.count() > 0 && now - last_watchdog_ >= watchdog_interval_) {
            sd_notify(0, "WATCHDOG=1");
            last_watchdog_ = now;
        }
    }

    Config cfg_;
    std::unique_ptr<LogSink> sink_;
    int sigfd_;
    SerialPort port_;
    FrameParser parser_;
    bool running_ = true;
    bool stale_ = false;
    Clock::time_point last_good_{};
    Clock::time_point last_rx_{};
    Clock::time_point last_watchdog_{};
    milliseconds watchdog_interval_{0};
};

} // namespace

int main(int argc, char** argv)
{
    Config cfg;
    if (!parse_args(argc, argv, cfg)) {
        usage(argv[0]);
        return 2;
    }

    const int sigfd = make_signalfd();
    if (sigfd < 0) {
        std::perror("signalfd");
        return 1;
    }

    auto sink = cfg.use_stderr ? make_stderr_sink() : make_journal_sink(cfg.device);
    Service svc(cfg, std::move(sink), sigfd);
    const int rc = svc.run();
    ::close(sigfd);
    return rc;
}

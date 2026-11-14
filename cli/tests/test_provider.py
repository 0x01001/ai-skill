"""Transport edge cases of one `/systemone` POST: slow bodies, malformed responses, retries."""

import socket
import threading
import time
import unittest
import urllib.request
from unittest import mock

from ai_skill_jev import provider


class RawServer:
    """A loopback server that answers every connection with `handler(conn)` (raw bytes)."""

    def __init__(self, handler):
        self.handler = handler
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.stop = threading.Event()

    def __enter__(self):
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.sock.close()

    def serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                try:
                    conn.recv(65536)  # the request; its content does not matter here
                    self.handler(conn, self.stop)
                except OSError:
                    pass


def trickle(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
    for _ in range(1000):
        if stop.wait(0.1):
            return
        conn.sendall(b" ")


def header_trickle(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\n")
    for n in range(1000):  # one header line every 0.1 s: each read is quick, the whole never ends
        if stop.wait(0.1):
            return
        conn.sendall(b"X-Slow-%d: a\r\n" % n)


def tls_trickle(conn, stop):
    # A TLS handshake record that announces 16 KiB, then one byte every 0.1 s: the client's
    # handshake reads keep getting data, so no socket timeout ever fires.
    conn.sendall(b"\x16\x03\x03\x40\x00")
    for _ in range(16384):
        if stop.wait(0.1):
            return
        conn.sendall(b"\x00")


def post_threads():
    return [t for t in threading.enumerate() if t.name == "ai-post" and t.is_alive()]


def bad_status_line(conn, stop):
    conn.sendall(b"NOT-HTTP garbage\r\n\r\n")


def huge_header(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nX-Big: " + b"a" * 70000 + b"\r\n\r\n")


class SendOnceTests(unittest.TestCase):
    def test_a_trickling_body_stops_at_the_timeout(self):
        with RawServer(trickle) as server:
            started = time.monotonic()
            status, body, _, reason = provider._send_once(server.url + "/systemone", b"{}", "k", 0.5)
            elapsed = time.monotonic() - started
        self.assertEqual((status, body, reason), (None, b"", "timeout"))
        self.assertLess(elapsed, 2.0)  # one byte every 0.1 s would take 100 s

    def test_trickling_headers_stop_at_the_timeout(self):
        with RawServer(header_trickle) as server:
            started = time.monotonic()
            status, body, _, reason = provider._send_once(server.url + "/systemone", b"{}", "k", 0.5)
            elapsed = time.monotonic() - started
            # the attempt left behind is cut, not left reading headers until the server stops
            give_up = time.monotonic() + 2.0
            while post_threads() and time.monotonic() < give_up:
                time.sleep(0.02)
            self.assertEqual(post_threads(), [])
        self.assertEqual((status, body, reason), (None, b"", "timeout"))
        self.assertLess(elapsed, 1.0)

    def test_a_trickling_tls_handshake_stops_at_the_timeout(self):
        with RawServer(tls_trickle) as server, \
                mock.patch.object(urllib.request, "getproxies", return_value={}):  # no proxy for 127.0.0.1
            url = server.url.replace("http://", "https://") + "/systemone"
            started = time.monotonic()
            status, body, _, reason = provider._send_once(url, b"{}", "k", 0.5)
            elapsed = time.monotonic() - started
        self.assertEqual((status, body, reason), (None, b"", "timeout"))
        self.assertLess(elapsed, 1.0)

    def test_abort_cuts_a_tls_handshake_in_progress(self):
        # CPython already bounds a whole handshake by the socket timeout; this checks the cut itself,
        # with a socket timeout far past the abort, so only the cut can end the handshake early.
        with RawServer(tls_trickle) as server, \
                mock.patch.object(urllib.request, "getproxies", return_value={}):
            url = server.url.replace("http://", "https://") + "/systemone"
            watch, box = provider._Watch(), {}
            request = urllib.request.Request(url, data=b"{}", method="POST")

            def attempt():
                try:
                    provider._opener(url, watch).open(request, timeout=30)
                except Exception as exc:
                    box["error"] = exc

            worker = threading.Thread(target=attempt, daemon=True)
            worker.start()
            time.sleep(0.3)  # mid-handshake: the server is sending one byte every 0.1 s
            self.assertTrue(worker.is_alive())
            watch.abort()
            worker.join(2.0)
            self.assertFalse(worker.is_alive(), "the handshake was not cut")
        self.assertIn("error", box)

    def test_the_item_deadline_covers_the_header_phase(self):
        with RawServer(header_trickle) as server:
            started = time.monotonic()
            res = provider.post_systemone(server.url, "m", "k", {"questions": {}}, deadline=time.monotonic() + 0.3)
            elapsed = time.monotonic() - started
        self.assertEqual((res.ok, res.reason, res.attempts), (False, "timeout", 1))
        self.assertLess(elapsed, 0.8)  # 20 s per attempt would apply without the item deadline

    def test_malformed_responses_are_network_errors_not_crashes(self):
        for handler in (bad_status_line, huge_header):
            with self.subTest(handler.__name__), RawServer(handler) as server:
                status, _, _, reason = provider._send_once(server.url + "/systemone", b"{}", "k", 3)
            self.assertEqual((status, reason), (None, "network_error"))

    def test_post_systemone_maps_a_malformed_response_to_provider_error(self):
        with RawServer(bad_status_line) as server:
            res = provider.post_systemone(server.url, "m", "k", {"questions": {}})
        self.assertEqual((res.ok, res.reason, res.attempts), (False, "provider_error", 1))


class Cancel:
    """A cancel event that fires during the first wait."""

    def __init__(self):
        self.fired = False

    def is_set(self):
        return self.fired

    def wait(self, seconds):
        self.fired = True
        return True


class RetryTests(unittest.TestCase):
    def send(self, *statuses):
        replies = iter(statuses)
        return mock.patch.object(provider, "_send_once",
                                 side_effect=lambda *a: (next(replies), b"", {"retry-after": "0"}, ""))

    def test_a_cancelled_wait_spends_no_retry_budget(self):
        taken = []
        with self.send(429, 429):
            res = provider.post_systemone("http://x", "m", "k", {}, retry_429=2, cancel=Cancel(),
                                          before_retry=lambda: taken.append(1) or True)
        self.assertEqual((res.reason, res.attempts, taken), ("cancelled", 1, []))

    def test_retry_budget_is_taken_after_the_wait(self):
        order = []
        with self.send(429, 200):
            res = provider.post_systemone("http://x", "m", "k", {}, retry_429=2,
                                          sleep=lambda s: order.append("sleep"),
                                          before_retry=lambda: order.append("budget") or True)
        self.assertEqual(order, ["sleep", "budget"])
        self.assertEqual((res.reason, res.attempts), ("provider_error", 2))  # empty 200 body


if __name__ == "__main__":
    unittest.main()

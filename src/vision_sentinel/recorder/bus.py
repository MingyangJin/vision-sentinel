"""Fan out frames from one decoder to many consumers.

Consumers never apply backpressure: a slow reader skips frames rather than
stalling the decoder. That is the right tradeoff for monitoring - a laggy
debug viewer must not be able to slow down the CV path.
"""

import threading


class FrameBus:
    """Holds the newest frame and wakes waiting consumers."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: bytes | None = None
        self._seq = 0

    def publish(self, frame: bytes) -> None:
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def wait(self, since: int, timeout: float = 10.0) -> tuple[bytes | None, int]:
        """Block until a frame newer than `since` exists. Returns (frame, seq)."""
        with self._cond:
            if self._seq == since:
                self._cond.wait(timeout)
            return self._frame, self._seq

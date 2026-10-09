"""A scripted terminal on pipes: the program reads what the test sends, the test reads its frames."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from tern_sdk import wire


@dataclass
class Sent:
    """One TSP message the program wrote."""

    verb: str
    params: dict[str, str]
    body: Any


HELLO = {
    "r": "hello",
    "v": 1,
    "term": "tern",
    "ver": "test",
    "kinds": list(wire.KINDS),
    "features": ["flow", "styles", "blobs"],
    "apc": 65536,
    "credits": 8,
    "cols": 120,
    "cell": {"w": 8, "h": 17},
    "dark": True,
    "reduceMotion": False,
}


class Term:
    """The terminal side of a TSP session."""

    def __init__(self) -> None:
        self.in_r, self.in_w = os.pipe()
        self.out_r, self.out_w = os.pipe()
        os.set_blocking(self.out_r, False)
        self._out = bytearray()

    def send(self, data: bytes) -> None:
        os.write(self.in_w, data)

    def reply(self, **override: Any) -> None:
        self.send(b"\x1b_tsp;r;" + json.dumps({**HELLO, **override}).encode() + b"\x1b\\")

    def da1(self) -> None:
        self.send(b"\x1b[?62;52;c")

    def event(self, **body: Any) -> None:
        self.send(b"\x1b_tsp;e;" + json.dumps(body).encode() + b"\x1b\\")

    def ack(self, sf: str, seq: int) -> None:
        self.event(ev="ack", sf=sf, s=seq)

    def key(self, text: str) -> None:
        self.send(text.encode())

    def read(self) -> bytes:
        """Everything written since the last read."""
        while True:
            try:
                chunk = os.read(self.out_r, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            self._out += chunk
        out, self._out = bytes(self._out), bytearray()
        return out

    def messages(self, data: bytes | None = None) -> list[Sent]:
        """The TSP messages in `data` (default: a fresh read), chunks joined."""
        data = self.read() if data is None else data
        out: list[Sent] = []
        joining: tuple[str, dict[str, str], bytearray] | None = None
        pos = 0
        while (start := data.find(b"\x1b_tsp;", pos)) >= 0:
            end = data.index(b"\x1b\\", start)
            message = wire.split(data[start + 6 : end])
            assert message is not None
            params = dict(message.params)
            pos = end + 2
            if "c" in params:
                if joining is None:
                    joining = (
                        message.verb,
                        {k: v for k, v in params.items() if k not in ("c", "m")},
                        bytearray(),
                    )
                joining[2].extend(message.body)
                if params.get("m") == "1":
                    continue
                verb, first, body = joining
                joining = None
                out.append(Sent(verb, first, _body(verb, bytes(body))))
                continue
            out.append(Sent(message.verb, params, _body(verb=message.verb, body=message.body)))
        return out

    def close(self) -> None:
        for fd in (self.in_r, self.in_w, self.out_r, self.out_w):
            try:
                os.close(fd)
            except OSError:
                pass


def _body(verb: str, body: bytes) -> Any:
    return body.decode("ascii") if verb == "b" else json.loads(body)

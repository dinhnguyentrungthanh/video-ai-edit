"""Remove a bounded PNG cover from TS segments of an explicitly opted-in provider."""
from __future__ import annotations

import struct
import zlib
from typing import Iterable, Iterator

from biliflow.download_http import HttpError

PNG = b"\x89PNG\r\n\x1a\n"
MAX_COVER = 1024 * 1024
MAX_PADDING = 4096
PACKET = 188


def ts_payload(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Streaming cover removal; the caller still validates every TS packet and the final size.

    Plain TS passes through. A PNG must have complete, CRC-valid chunks, a first IHDR and a zero-length
    IEND. Only the next 4096 bytes may be padding, followed by at least five TS sync marks. No PNG-only
    response or arbitrary image scanning is accepted. At most one cover plus one incoming chunk is held.
    """
    pending = bytearray()
    position, end, plain = 8, None, False
    iterator = iter(chunks)

    def bad() -> HttpError:
        return HttpError("SEGMENT_NOT_TS", "Đoạn bọc PNG thiếu MPEG-TS hợp lệ; không ghép.", retryable=True)

    for chunk in iterator:
        pending.extend(chunk)
        if len(pending) < 8:
            continue
        if position == 8 and end is None and pending[:8] != PNG:
            plain = True
        if plain:
            yield bytes(pending)
            yield from iterator
            return
        while end is None and position + 12 <= len(pending):
            length = struct.unpack_from(">I", pending, position)[0]
            stop = position + length + 12
            kind = bytes(pending[position + 4:position + 8])
            if stop > MAX_COVER or (position == 8 and (kind != b"IHDR" or length != 13)):
                raise bad()
            if stop > len(pending):
                break
            expected = struct.unpack_from(">I", pending, stop - 4)[0]
            if zlib.crc32(pending[position + 4:stop - 4]) != expected:
                raise bad()
            position = stop
            if kind == b"IEND":
                if length:
                    raise bad()
                end = stop
        if end is not None:
            last = min(end + MAX_PADDING, len(pending) - 4 * PACKET - 1)
            for offset in range(end, last + 1):
                if all(pending[offset + i * PACKET] == 0x47 for i in range(5)):
                    yield bytes(pending[offset:])
                    yield from iterator
                    return
            if len(pending) > end + MAX_PADDING + 5 * PACKET:
                raise bad()
        elif len(pending) > MAX_COVER:
            raise bad()
    raise bad()

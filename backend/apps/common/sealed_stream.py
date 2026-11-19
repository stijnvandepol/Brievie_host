"""A stream sealed in chunks, so a large file never exists whole in plaintext on disk or
whole in memory (an export of a member's post can hold hundreds of page images).

Format: a series of chunks, each a 4-byte big-endian length and `crypto.seal` of up to
4 MiB of data. A chunk is sealed with the context `aad | index | last`, so a chunk
cannot move, repeat or be cut off: a stream that ends without its last chunk, or has
its chunks reordered, changed or repeated, does not open.
"""

import io
from collections.abc import Iterator
from types import TracebackType
from typing import BinaryIO

from apps.common import crypto

CHUNK_BYTES = 4 * 1024 * 1024
_LENGTH_BYTES = 4
# Sealing adds a header, a nonce and a tag; anything longer than this is not ours.
_MAX_SEALED = CHUNK_BYTES + 64


def _chunk_aad(aad: bytes, index: int, *, last: bool) -> bytes:
    return aad + b"|" + index.to_bytes(8, "big") + b"|" + (b"1" if last else b"0")


class SealedWriter(io.RawIOBase):
    """Writes what it is given to `target`, sealed in chunks.

    A chunk goes out only when more data follows it, so the last chunk is known when
    `close()` writes it. Leaving a `with` block through an exception does not write it:
    what was written then never opens as a whole. `target` stays open (the caller owns it).
    """

    def __init__(self, target: BinaryIO, key: bytes, *, aad: bytes) -> None:
        super().__init__()
        self._target = target
        self._key = key
        self._aad = aad
        self._buffer = bytearray()
        self._index = 0
        self._finished = False

    def writable(self) -> bool:
        return True

    def write(self, data: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        if self.closed or self._finished:
            raise ValueError("write to a closed sealed stream")
        view = memoryview(data).cast("B")
        self._buffer += view
        # Keep at least one byte back: the chunk that ends the stream is sealed as the last.
        while len(self._buffer) > CHUNK_BYTES:
            self._emit(bytes(self._buffer[:CHUNK_BYTES]), last=False)
            del self._buffer[:CHUNK_BYTES]
        return len(view)

    def abort(self) -> None:
        """Stop without sealing a last chunk: the file is then known to be incomplete."""
        self._finished = True
        self._buffer.clear()

    def __del__(self) -> None:
        # Garbage-collected without `close()`: the writer was dropped, usually by a failure.
        # `IOBase.__del__` would close it and seal a valid last chunk, which makes a
        # truncated file look whole.
        if not self._finished:
            self.abort()
        super().__del__()

    def close(self) -> None:
        if not self.closed and not self._finished:
            self._finished = True
            self._emit(bytes(self._buffer), last=True)
            self._buffer.clear()
        super().close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.abort()
        self.close()

    def _emit(self, chunk: bytes, *, last: bool) -> None:
        sealed = crypto.seal(self._key, chunk, aad=_chunk_aad(self._aad, self._index, last=last))
        self._target.write(len(sealed).to_bytes(_LENGTH_BYTES, "big"))
        self._target.write(sealed)
        self._index += 1


def _read_length(source: BinaryIO) -> int | None:
    """The next chunk's length, or None at the clean end of the stream."""
    header = source.read(_LENGTH_BYTES)
    if not header:
        return None
    if len(header) < _LENGTH_BYTES:
        raise crypto.DecryptionError("The sealed stream is cut off.")
    length = int.from_bytes(header, "big")
    if length > _MAX_SEALED:
        raise crypto.DecryptionError("Not a sealed stream.")
    return length


def _read_chunk(source: BinaryIO, length: int) -> bytes:
    sealed = source.read(length)
    if len(sealed) < length:
        raise crypto.DecryptionError("The sealed stream is cut off.")
    return sealed


def iter_opened(source: BinaryIO, key: bytes, *, aad: bytes) -> Iterator[bytes]:
    """The plain data of a stream written by `SealedWriter`, one chunk at a time.

    A chunk is given out only after it is checked, and the one before the end of the
    stream must be the one sealed as the last. Raises `crypto.DecryptionError` for a
    changed, reordered, repeated or missing chunk, a stream that ends without its last
    chunk, and an empty one.
    """
    length = _read_length(source)
    if length is None:
        raise crypto.DecryptionError("The sealed stream is empty.")
    index = 0
    while True:
        sealed = _read_chunk(source, length)
        # The chunk is the last one when nothing follows it; the seal says if it is right.
        following = _read_length(source)
        last = following is None
        yield crypto.open_sealed(key, sealed, aad=_chunk_aad(aad, index, last=last))
        if following is None:
            return
        length = following
        index += 1

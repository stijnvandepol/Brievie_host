import io
import os

import pytest

from apps.common import crypto
from apps.common.sealed_stream import SealedWriter, iter_opened

KEY = bytes(range(32))
AAD = b"export/1"
MIB = 1024 * 1024


def seal_stream(data: bytes, *, key: bytes = KEY, aad: bytes = AAD, piece: int = 1 * MIB) -> bytes:
    target = io.BytesIO()
    writer = SealedWriter(target, key, aad=aad)
    for start in range(0, len(data), piece):
        writer.write(data[start : start + piece])
    writer.close()
    return target.getvalue()


def chunks_of(sealed: bytes) -> list[bytes]:
    """The length-prefixed chunks of a sealed stream, each with its prefix."""
    found: list[bytes] = []
    position = 0
    while position < len(sealed):
        length = int.from_bytes(sealed[position : position + 4], "big")
        found.append(sealed[position : position + 4 + length])
        position += 4 + length
    return found


def opened(sealed: bytes, *, key: bytes = KEY, aad: bytes = AAD) -> bytes:
    return b"".join(iter_opened(io.BytesIO(sealed), key, aad=aad))


def test_round_trip_over_several_chunks() -> None:
    data = os.urandom(9 * MIB)

    sealed = seal_stream(data)

    assert len(chunks_of(sealed)) == 3
    assert data[:64] not in sealed
    assert opened(sealed) == data


def test_the_empty_stream_and_the_exact_chunk_size_round_trip() -> None:
    assert opened(seal_stream(b"")) == b""
    exact = os.urandom(4 * MIB)
    assert opened(seal_stream(exact)) == exact


def test_a_writer_never_holds_more_than_a_chunk_and_close_is_idempotent() -> None:
    target = io.BytesIO()
    writer = SealedWriter(target, KEY, aad=AAD)

    writer.write(os.urandom(5 * MIB))
    # One whole chunk went out, the rest waits for more or for the last.
    assert len(chunks_of(target.getvalue())) == 1
    writer.close()
    writer.close()

    assert len(chunks_of(target.getvalue())) == 2


def test_a_missing_last_chunk_fails() -> None:
    sealed = seal_stream(os.urandom(9 * MIB))
    first = chunks_of(sealed)[0]

    with pytest.raises(crypto.DecryptionError):
        opened(first)
    with pytest.raises(crypto.DecryptionError):
        opened(b"".join(chunks_of(sealed)[:2]))


def test_nothing_unverified_is_given_out() -> None:
    sealed = seal_stream(os.urandom(9 * MIB))
    stream = iter_opened(io.BytesIO(b"".join(chunks_of(sealed)[:2])), KEY, aad=AAD)

    # The first chunk is only known to be a middle one once the next header is read, and
    # the cut stream ends on a chunk that was not sealed as the last: it fails at the end,
    # having given out nothing it could not check.
    got: list[bytes] = []

    def drain() -> None:
        got.extend(stream)

    with pytest.raises(crypto.DecryptionError):
        drain()
    assert sum(len(piece) for piece in got) <= 4 * MIB


def test_an_empty_source_is_not_a_sealed_stream() -> None:
    with pytest.raises(crypto.DecryptionError):
        opened(b"")


def test_reordered_chunks_fail() -> None:
    first, second, last = chunks_of(seal_stream(os.urandom(9 * MIB)))

    with pytest.raises(crypto.DecryptionError):
        opened(second + first + last)


def test_a_repeated_chunk_fails() -> None:
    first, second, last = chunks_of(seal_stream(os.urandom(9 * MIB)))

    with pytest.raises(crypto.DecryptionError):
        opened(first + first + second + last)


def test_a_changed_byte_fails() -> None:
    sealed = bytearray(seal_stream(os.urandom(5 * MIB)))
    sealed[100] ^= 1

    with pytest.raises(crypto.DecryptionError):
        opened(bytes(sealed))


def test_another_aad_or_key_fails() -> None:
    sealed = seal_stream(os.urandom(2 * MIB))

    with pytest.raises(crypto.DecryptionError):
        opened(sealed, aad=b"export/2")
    with pytest.raises(crypto.DecryptionError):
        opened(sealed, key=bytes(32))


def test_a_cut_inside_a_chunk_and_a_lying_length_fail() -> None:
    sealed = seal_stream(os.urandom(2 * MIB))

    with pytest.raises(crypto.DecryptionError):
        opened(sealed[:-10])
    with pytest.raises(crypto.DecryptionError):
        opened(sealed[:2])
    # A header that claims gigabytes is refused before anything is read for it.
    with pytest.raises(crypto.DecryptionError):
        opened((0xFFFFFFFF).to_bytes(4, "big") + b"x" * 100)


def test_a_failed_export_is_not_closed_as_a_complete_one() -> None:
    target = io.BytesIO()

    def failing_export() -> None:
        with SealedWriter(target, KEY, aad=AAD) as writer:
            writer.write(os.urandom(5 * MIB))
            raise RuntimeError("the export failed")

    with pytest.raises(RuntimeError):
        failing_export()

    # No chunk was sealed as the last: the file cannot be read back as a whole.
    with pytest.raises(crypto.DecryptionError):
        opened(target.getvalue())

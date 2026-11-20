"""The structure of an uploaded page image, checked before anything is stored or queued.

The server never decodes a page (no image library; the AI service and the apps do).
It does check what any decoder needs before the picture data: the start of image, a
frame header with a width and height within limits, and a whole scan header. That
stops a page that is no picture at all (three magic bytes, a file cut off early) from
being stored and sent to a paid AI service. Pure: bytes in, size out.
"""

# Larger than any scan of a page; a decoder of a bigger header could be made to
# allocate gigabytes.
MAX_SIDE = 20_000
MAX_PIXELS = 100_000_000

_SOI = b"\xff\xd8"
_EOI = 0xD9
_SOS = 0xDA
# Frame headers: SOF0-SOF15 without DHT (C4), JPG (C8) and DAC (CC).
_FRAMES = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers without a length: TEM and the restart markers.
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a structurally whole JPEG, or None.

    Walks the segments from the start of image to the first scan header.
    """
    if not data.startswith(_SOI):
        return None
    size: tuple[int, int] | None = None
    position = len(_SOI)
    while position < len(data):
        if data[position] != 0xFF:
            return None
        while position < len(data) and data[position] == 0xFF:
            position += 1  # fill bytes
        if position >= len(data):
            return None
        marker = data[position]
        position += 1
        if marker in _STANDALONE:
            continue
        if marker == _EOI or position + 2 > len(data):
            return None
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            return None
        if marker in _FRAMES:
            # One frame per picture: with a second, decoders differ in which they use.
            if length < 8 or size is not None:
                return None
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            size = (width, height)
        elif marker == _SOS:
            return size if size is not None and _sane(size) else None
        position += length
    return None


def _sane(size: tuple[int, int]) -> bool:
    width, height = size
    return 0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS

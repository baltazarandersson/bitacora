"""Draw the app icon from the scene's lighthouse: python scripts/make_icon.py -> packaging/bitacora.ico"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from bitacora.ui.scene import (
    DOOR,
    IRON,
    LIGHT,
    MOON_LIT,
    RED,
    ROCK,
    ROOF,
    SEA_TOP,
    SKY_LOW,
    SKY_TOP,
    TOWER,
    WHITE,
    lerp,
)

ROOT = Path(__file__).resolve().parent.parent
N = 32  # logical pixels


def canvas() -> list[list[tuple[int, int, int, int]]]:
    img = [[(0, 0, 0, 0)] * N for _ in range(N)]
    for y in range(N):
        for x in range(N):
            # rounded square
            cx, cy = min(x, N - 1 - x), min(y, N - 1 - y)
            if cx < 3 and cy < 3 and (3 - cx) ** 2 + (3 - cy) ** 2 > 10:
                continue
            c = lerp(SKY_TOP[0], SKY_LOW[0], y / (N - 1)) if y < 27 else SEA_TOP[0]
            img[y][x] = (*c, 255)
    lamp_x, lamp_y = 3 + 6, 2 + 4  # the tower is drawn from x=3, y=2
    for y in range(N):  # beam
        for x in range(lamp_x + 1, N):
            dy = (y - lamp_y) / (x - lamp_x)
            if 0.08 < dy < 0.5:
                k = (1 - abs(dy - 0.29) / 0.21) * 0.6
                if k > 0 and img[y][x][3]:
                    img[y][x] = (*lerp(img[y][x][:3], LIGHT, k), 255)
    mx, my = MOON_AT
    for j, line in enumerate(MOON_PIXELS):
        for i, ch in enumerate(line):
            if ch == "#":
                img[my + j][mx + i] = (*MOON_LIT, 255)
    colors = {"roof": ROOF, "iron": IRON, "lamp": LIGHT, "white": WHITE, "red": RED, "rock": ROCK, "door": DOOR}
    for x, y, part, shade in TOWER:
        c = colors[part]
        if part in ("red", "white", "roof", "rock"):
            c = lerp(lerp(c, (0, 0, 0), 0.5), c, 0.3 + 0.7 * shade)
        img[2 + y][9 - 6 + x] = (*c, 255)
    return img


# The moon in pixel art like the tower: a symmetric crescent, three pixels thick.
MOON_PIXELS = [
    "..###..",
    ".###...",
    "###....",
    "###....",
    "###....",
    ".###...",
    "..###..",
]
MOON_AT = (22, 2)  # top-left corner, clear of the beam


def scaled(img, size: int) -> bytes:
    """Pixel art scaled with nearest neighbour."""
    rows = []
    for y in range(size):
        sy = y * N // size
        row = bytearray([0])  # PNG filter: none
        for x in range(size):
            row += bytes(img[sy][x * N // size])
        rows.append(bytes(row))
    return b"".join(rows)


def png(img, size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scaled(img, size), 9))
            + chunk(b"IEND", b""))


def ico(img, sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = [png(img, s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images, strict=True):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


if __name__ == "__main__":
    out = ROOT / "packaging" / "bitacora.ico"
    out.parent.mkdir(exist_ok=True)
    img = canvas()
    out.write_bytes(ico(img))
    (ROOT / "packaging" / "bitacora.png").write_bytes(png(img, 256))
    print(out)

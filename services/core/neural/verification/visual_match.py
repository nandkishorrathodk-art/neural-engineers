"""Compare two independently captured desktop frames for independent verification.

A live desktop is never byte-identical between two captures: a text caret blinks,
a clock ticks, and the pointer moves. Requiring an exact pixel-hash match between
the worker's post-action frame and the verifier's own frame therefore fails closed
on genuine, correct work. Verification instead accepts an exact match, or a
perceptual match (whole-screen dHash within a small Hamming distance), which still
rejects a genuinely different screen -- those differ in tens of bits.
"""

from __future__ import annotations

import numpy as np

# 64-bit dHash budget. Caret/clock/cursor churn perturbs only a few bits; a
# different application or desktop differs in far more.
MAX_PERCEPTUAL_HAMMING_DISTANCE = 8


def frame_dhash(pixels: np.ndarray, hash_size: int = 8) -> str:
    """64-bit difference hash of a whole RGB frame as zero-padded hex."""
    height, width = pixels.shape[:2]
    if height < hash_size or width < hash_size + 1:
        return "0" * (hash_size * hash_size // 4)
    ys = np.linspace(0, height - 1, hash_size, dtype=int)
    xs = np.linspace(0, width - 1, hash_size + 1, dtype=int)
    grid = pixels[np.ix_(ys, xs)].astype(np.float32)
    gray = 0.299 * grid[:, :, 0] + 0.587 * grid[:, :, 1] + 0.114 * grid[:, :, 2]
    diff = gray[:, 1:] > gray[:, :-1]
    return format(int("".join("1" if bit else "0" for bit in diff.flatten()), 2), "x").zfill(
        hash_size * hash_size // 4
    )


def hamming_distance_hex(a: str | None, b: str | None) -> int | None:
    """Bit distance between two equal-length hex hashes, or None if not comparable."""
    if not a or not b or len(a) != len(b):
        return None
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return None


def frames_visually_match(
    worker_hash: str | None,
    worker_phash: str | None,
    live_hash: str | None,
    live_phash: str | None,
) -> tuple[bool, str]:
    """Return (matched, method) for a worker frame and an independent frame."""
    if worker_hash and live_hash and worker_hash == live_hash:
        return True, "exact"
    distance = hamming_distance_hex(worker_phash, live_phash)
    if distance is not None and distance <= MAX_PERCEPTUAL_HAMMING_DISTANCE:
        return True, "perceptual"
    return False, "mismatch"

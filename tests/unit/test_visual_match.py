import numpy as np
from neural.verification.visual_match import (
    MAX_PERCEPTUAL_HAMMING_DISTANCE,
    frame_dhash,
    frames_visually_match,
    hamming_distance_hex,
)


def test_exact_hash_match_wins_without_perceptual_hashes():
    matched, method = frames_visually_match("a" * 64, None, "a" * 64, None)
    assert matched is True
    assert method == "exact"


def test_perceptual_match_tolerates_small_live_desktop_churn():
    matched, method = frames_visually_match(
        "a" * 64, "0000000000000000", "c" * 64, "00000000000000ff"
    )
    assert matched is True
    assert method == "perceptual"


def test_perceptual_mismatch_fails_closed_beyond_budget():
    far = "f" * (MAX_PERCEPTUAL_HAMMING_DISTANCE + 8)
    matched, method = frames_visually_match("a" * 64, "0" * 16, "c" * 64, far)
    assert matched is False
    assert method == "mismatch"


def test_missing_perceptual_hashes_never_match_a_different_frame():
    matched, method = frames_visually_match("a" * 64, None, "c" * 64, None)
    assert matched is False
    assert method == "mismatch"


def test_hamming_distance_hex_rejects_incomparable_values():
    assert hamming_distance_hex("", "ff") is None
    assert hamming_distance_hex("ff", "fff") is None
    assert hamming_distance_hex("zz", "ff") is None
    assert hamming_distance_hex("ff", "ff") == 0


def test_frame_dhash_is_stable_for_identical_frames():
    pixels = np.zeros((40, 60, 3), dtype=np.uint8)
    pixels[:, 30:, :] = 255
    assert frame_dhash(pixels) == frame_dhash(pixels.copy())


def test_frame_dhash_handles_frames_smaller_than_hash_grid():
    assert frame_dhash(np.zeros((4, 4, 3), dtype=np.uint8)) == "0" * 16

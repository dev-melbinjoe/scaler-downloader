"""One screenshot per slide / screen the instructor showed.

Only keyframes are decoded (fast even on a laptop CPU). A new "scene" starts when the screen
changes noticeably; each scene is saved as its *last* frame, i.e. the slide or board in its most
complete state, just before the instructor moved on.
"""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np

from .util import hms, write_json

THUMB = (160, 90)
INK = 20  # grey-level change that counts as "this pixel changed" (thin pen strokes are faint)
# Tuned on a real class (whiteboard notebook + code editor + webcam corner):
CUT = 0.15  # share of pixels changing between two keyframes: a different screen
# Share changed since the scene began, and how long the scene must have run before it counts:
# a scrolled notebook reaches 6-11% (a shot every ~2 min); edited code in an IDE only 2-3%
# (a shot every ~4 min); a static slide stays near 0.
DRIFT_RULES = ((120.0, 0.05), (240.0, 0.015))


def _changed(a: np.ndarray, b: np.ndarray, static: np.ndarray) -> float:
    return float((np.abs(a - b) > INK)[static].mean()) if static.any() else 1.0


def _nothing_to_read(frame) -> bool:
    """Blank screen (sharing paused) or the instructor's camera filling the screen."""
    rgb = frame.to_ndarray(width=THUMB[0], height=THUMB[1], format="rgb24").astype(np.int16)
    gray = rgb.mean(axis=2)
    if gray.std() < 10:
        return True
    # Screens (slides, boards, IDEs) are mostly perfectly flat, grey-ish UI; a camera picture is
    # noisy and colourful. Measured: screens 55-70% flat & colour 4-13, camera 40% flat & colour 30.
    gx, gy = np.abs(np.diff(gray, axis=1))[:-1, :], np.abs(np.diff(gray, axis=0))[:, :-1]
    flat = ((gx < 3) & (gy < 3)).mean()
    colour = (rgb.max(axis=2) - rgb.min(axis=2)).mean()
    return flat < 0.45 and colour > 20


def extract_slides(
    video: Path,
    out_dir: Path,
    min_scene: float = 15.0,
    max_frames: int = 150,
    width: int = 1280,
) -> list[dict]:
    """Writes frames/*.jpg + frames.json next to the video and returns the frame list."""
    folder = out_dir / "frames"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    kept: list[np.ndarray] = []

    def save(start: float, end: float, frame, thumb: np.ndarray, static: np.ndarray) -> None:
        if end - start < min_scene:
            return
        if any(_changed(thumb, k, static) < 0.02 for k in kept[-8:]):  # back on an earlier slide
            return
        if _nothing_to_read(frame):
            return
        image = frame.to_image()
        if image.width > width:
            image = image.resize((width, round(image.height * width / image.width)))
        name = f"{hms(start).replace(':', '-')}.jpg"
        image.save(folder / name, quality=70, optimize=True)
        kept.append(thumb)
        saved.append({"start": round(start, 1), "end": round(end, 1), "file": f"frames/{name}"})

    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        stream.codec_context.skip_frame = "NONKEY"
        prev = ref = None
        prev_frame = None
        # Pixels that change on most keyframes are live video (the instructor's webcam): ignore them.
        live = np.zeros((THUMB[1], THUMB[0]))
        static = np.ones_like(live, dtype=bool)
        scene_start = t = 0.0
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            t = float(frame.time)
            thumb = frame.to_ndarray(width=THUMB[0], height=THUMB[1], format="gray").astype(np.int16)
            if prev is None:
                ref = thumb
            else:
                live = 0.8 * live + 0.2 * (np.abs(thumb - prev) > INK)
                static = live < 0.3
                age = t - scene_start
                if _changed(thumb, prev, static) > CUT or any(
                    age > after and _changed(thumb, ref, static) > share for after, share in DRIFT_RULES
                ):
                    save(scene_start, t, prev_frame, prev, static)
                    scene_start, ref = t, thumb
            prev, prev_frame = thumb, frame
        if prev_frame is not None:
            save(scene_start, t, prev_frame, prev, static)

    if len(saved) > max_frames:  # keep the screens that stayed up longest
        keep = sorted(sorted(saved, key=lambda f: f["end"] - f["start"], reverse=True)[:max_frames], key=lambda f: f["start"])
        for f in saved:
            if f not in keep:
                (out_dir / f["file"]).unlink(missing_ok=True)
        saved = keep
    write_json(out_dir / "frames.json", saved)
    return saved

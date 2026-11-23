"""Compare two screenshots, ported from the render half of ``core/render.py``.

A claim about appearance ("the panel is positioned left of the map", "it looks
like the reference") cannot be settled by numbers alone, but it can be made
mechanical: render both images and measure how many pixels changed and how far.
This module does that with the standard library, so a Verifier can turn a visual
requirement into a threshold instead of an opinion.
"""
from eyes import parse_ppm


class RenderError(RuntimeError):
    """An image could not be read or the two images are not comparable."""


def load(path):
    with open(path, "rb") as handle:
        return parse_ppm(handle.read())


def compare(path_a, path_b):
    """Compare two PPM screenshots; return a measurement dict."""
    width_a, height_a, pixels_a = load(path_a)
    width_b, height_b, pixels_b = load(path_b)
    if (width_a, height_a) != (width_b, height_b):
        return {"width": [width_a, width_b], "height": [height_a, height_b],
                "comparable": False, "changed_ratio": 1.0, "mean_diff": 255.0}
    total = width_a * height_a
    changed = 0
    accumulated = 0
    for index in range(0, total * 3, 3):
        diff = (abs(pixels_a[index] - pixels_b[index])
                + abs(pixels_a[index + 1] - pixels_b[index + 1])
                + abs(pixels_a[index + 2] - pixels_b[index + 2]))
        accumulated += diff
        if diff > 30:                     # a per-pixel visibility threshold
            changed += 1
    return {"width": [width_a, width_b], "height": [height_a, height_b],
            "comparable": True,
            "changed_ratio": changed / total if total else 0.0,
            "mean_diff": accumulated / (total * 3) if total else 0.0}


def same(path_a, path_b, *, max_changed_ratio=0.02, max_mean_diff=8.0):
    """Whether two renders are close enough to call equivalent."""
    result = compare(path_a, path_b)
    if not result["comparable"]:
        return False, result
    ok = (result["changed_ratio"] <= max_changed_ratio
          and result["mean_diff"] <= max_mean_diff)
    return ok, result


def changed_regions(path_a, path_b, *, tile=64, threshold=0.25):
    """Coarse map of changed tiles, for telling the Actor *where* it differs."""
    width_a, height_a, pixels_a = load(path_a)
    width_b, height_b, pixels_b = load(path_b)
    if (width_a, height_a) != (width_b, height_b):
        raise RenderError("images differ in size; render the same viewport")
    regions = []
    for top in range(0, height_a, tile):
        for left in range(0, width_a, tile):
            changed = total = 0
            for y in range(top, min(top + tile, height_a)):
                row = y * width_a * 3
                for x in range(left, min(left + tile, width_a)):
                    index = row + x * 3
                    total += 1
                    diff = (abs(pixels_a[index] - pixels_b[index])
                            + abs(pixels_a[index + 1] - pixels_b[index + 1])
                            + abs(pixels_a[index + 2] - pixels_b[index + 2]))
                    if diff > 30:
                        changed += 1
            if total and changed / total >= threshold:
                regions.append({"x": left, "y": top,
                                "changed_ratio": round(changed / total, 3)})
    return regions

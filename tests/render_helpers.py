"""Shared helpers for render-to-PNG reference tests.

assert_render_matches takes a pre-rendered PIL Image and either saves it as a
new reference (first run or --update-refs) or asserts a pixel-exact match
against the committed reference PNG.
"""
from pathlib import Path

from PIL import Image, ImageChops

REFS_DIR = Path(__file__).parent / "reference-images"


def pixel_diff_message(img, ref_img, name, hint="run pytest --update-refs to accept new output"):
    """Build a detailed assertion message describing a pixel mismatch.

    Reports the image dimensions (or a size mismatch), how many pixels differ,
    and the coordinates and RGB values of the first differing pixel.
    """
    if img.size != ref_img.size:
        return (
            f"Render mismatch for '{name}': "
            f"size changed from {ref_img.size} to {img.size} — {hint}"
        )

    diff = ImageChops.difference(img, ref_img)
    # get_flattened_data() returns a sequence of per-pixel tuples, e.g. (R,G,B).
    pixels = diff.get_flattened_data()
    w, h = img.size

    n_diff = 0
    first_col = first_row = ref_px = got_px = None
    for i, px_diff in enumerate(pixels):
        if any(px_diff):
            if n_diff == 0:
                first_col, first_row = i % w, i // w
                ref_px = ref_img.getpixel((first_col, first_row))
                got_px = img.getpixel((first_col, first_row))
            n_diff += 1

    if n_diff == 0:
        return f"Render mismatch for '{name}' (pixel data matched — metadata difference?)"

    return (
        f"Render mismatch for '{name}': {n_diff}/{w * h} pixels differ; "
        f"first at ({first_col},{first_row}) ref={ref_px} got={got_px} — {hint}"
    )


def assert_render_matches(request, image, name, state_dict=None):
    """Assert a PIL Image matches the reference fixture PNG (or save it as new).

    If the reference does not exist (first run) or --update-refs is passed,
    the image is saved as the new reference and the test passes.
    Otherwise a pixel-exact comparison is performed against the saved file.
    The pixel comparison uses only pixel data — embedded metadata is ignored.

    Args:
        request:    The pytest ``request`` fixture.
        image:      A PIL Image of the current display state.
        name:       Base name for the reference PNG (no extension).
        state_dict: Optional dict from ``snapshot_state()``; when provided,
                    it is serialized as TOML and embedded in saved PNGs as
                    an iTXt chunk under the key 'weatherpanel:state'.
    """
    img = image
    ref_path = REFS_DIR / f"{name}.png"

    if request.config.getoption("--update-refs") or not ref_path.exists():
        REFS_DIR.mkdir(exist_ok=True)
        if state_dict is not None:
            from state_snapshot import make_png_info
            img.save(ref_path, pnginfo=make_png_info(state_dict))
        else:
            img.save(ref_path)
        return

    ref_img = Image.open(ref_path).convert("RGB")
    assert list(img.get_flattened_data()) == list(ref_img.get_flattened_data()), (
        pixel_diff_message(img, ref_img, name)
    )

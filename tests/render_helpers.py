"""Shared helpers for render-to-PNG reference tests.

compare_or_save renders the current display state to a PIL Image, then either
saves it as a new reference (first run or --update-refs) or asserts a
pixel-exact match against the committed reference PNG.
"""
from pathlib import Path

from PIL import Image

REFS_DIR = Path(__file__).parent / "reference-images"


def compare_or_save(request, display_obj, name):
    """Render display_obj to a PNG and compare against the reference fixture.

    If the reference does not exist (first run) or --update-refs is passed,
    the rendered image is saved as the new reference and the test passes.
    Otherwise a pixel-exact comparison is performed against the saved file.
    """
    img = display_obj._display.render_to_image(scale=8)
    ref_path = REFS_DIR / f"{name}.png"

    if request.config.getoption("--update-refs") or not ref_path.exists():
        REFS_DIR.mkdir(exist_ok=True)
        img.save(ref_path)
        return

    ref_img = Image.open(ref_path).convert("RGB")
    assert list(img.getdata()) == list(ref_img.getdata()), (
        f"Render mismatch for '{name}' — run pytest --update-refs to accept new output"
    )

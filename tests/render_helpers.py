"""Shared helpers for render-to-PNG reference tests.

compare_or_save renders the current display state to a PIL Image, then either
saves it as a new reference (first run or --update-refs) or asserts a
pixel-exact match against the committed reference PNG.
"""
from pathlib import Path

from PIL import Image

REFS_DIR = Path(__file__).parent / "reference-images"


def compare_or_save(request, display_obj, name, state_dict=None):
    """Render display_obj to a PNG and compare against the reference fixture.

    If the reference does not exist (first run) or --update-refs is passed,
    the rendered image is saved as the new reference and the test passes.
    Otherwise a pixel-exact comparison is performed against the saved file.
    The pixel comparison uses only pixel data — embedded metadata is ignored.

    Args:
        request:     The pytest ``request`` fixture.
        display_obj: A Display instance backed by a SimDisplay.
        name:        Base name for the reference PNG (no extension).
        state_dict:  Optional dict from ``snapshot_state()``; when provided,
                     it is serialized as TOML and embedded in saved PNGs as
                     an iTXt chunk under the key 'weatherpanel:state'.
    """
    img = display_obj._display.render_to_image(scale=8)
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
        f"Render mismatch for '{name}' — run pytest --update-refs to accept new output"
    )

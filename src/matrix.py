"""Hardware setup for 64x32 RGB LED matrix display.

Configures pins and initializes the rgbmatrix driver for CircuitPython.
"""
import board # type: ignore
import displayio
import rgbmatrix
import framebufferio

def display_set_root(root_group,_rotation=None,swapgb=False):
    """Initialize RGB matrix hardware and attach displayio group tree.

    Args:
        root_group: Top-level displayio.Group to render
        swapgb: Some matrix panels have green/blue wiring reversed

    Returns:
        framebufferio.FramebufferDisplay: the active display object, needed
        to call display.refresh() and work around a CircuitPython 10 rendering
        bug where replacing TileGrid items in a Group does not automatically
        mark the framebuffer region as dirty.
    """

    displayio.release_displays()

    if swapgb:
        rgb_pins=[
            board.MTX_R1,
            board.MTX_B1,
            board.MTX_G1,
            board.MTX_R2,
            board.MTX_B2,
            board.MTX_G2,
        ]
    else:
        rgb_pins=[
            board.MTX_R1,
            board.MTX_G1,
            board.MTX_B1,
            board.MTX_R2,
            board.MTX_G2,
            board.MTX_B2
        ]


    matrix = rgbmatrix.RGBMatrix(
        width=64, bit_depth=6,
        rgb_pins=rgb_pins,
        addr_pins=[
            board.MTX_ADDRA,
            board.MTX_ADDRB,
            board.MTX_ADDRC,
            board.MTX_ADDRD
        ],
        clock_pin=board.MTX_CLK,
        latch_pin=board.MTX_LAT,
        output_enable_pin=board.MTX_OE
    )
    display = framebufferio.FramebufferDisplay(matrix)
    display.root_group=root_group
    return display

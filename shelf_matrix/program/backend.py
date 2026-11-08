"""Matrix backend: the real rgbmatrix library, or a PIL-backed fake."""
from __future__ import annotations

import os

from PIL import Image

from shelf_matrix.config import MatrixOptions


class FakeCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.image = Image.new("RGB", (width, height))

    def SetImage(self, img: Image.Image) -> None:
        self.image = img.copy()


class FakeMatrix:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.brightness = 100
        self.last_image: Image.Image | None = None

    def CreateFrameCanvas(self) -> FakeCanvas:
        return FakeCanvas(self.width, self.height)

    def SwapOnVSync(self, canvas: FakeCanvas) -> FakeCanvas:
        self.last_image = canvas.image
        png = os.environ.get("SHELF_MATRIX_FAKE_PNG")
        if png:
            canvas.image.save(png)
        return canvas


def create_matrix(options: MatrixOptions, brightness: int):
    if os.environ.get("SHELF_MATRIX_FAKE") == "1":
        m = FakeMatrix(options.cols, options.rows)
        m.brightness = brightness
        return m
    from rgbmatrix import RGBMatrix, RGBMatrixOptions  # only importable on the Pi

    opts = RGBMatrixOptions()
    options.apply_to(opts)
    opts.brightness = brightness
    return RGBMatrix(options=opts)

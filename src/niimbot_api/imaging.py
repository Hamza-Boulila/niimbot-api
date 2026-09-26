"""Turn an arbitrary image into a 1-bit bitmap that fits a Niimbot label."""

from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps

DOTS_PER_MM = 8  # ~203 dpi, same for all supported models

FitMode = Literal["contain", "cover", "stretch", "none"]


@dataclass(frozen=True)
class PrinterModel:
    name: str
    max_width_px: int  # print head width
    max_density: int
    protocol: str = "v1"  # "v2" = newer print flow (B1)


MODELS = {
    "b1": PrinterModel("b1", 384, 5, "v2"),
    "b18": PrinterModel("b18", 384, 3),
    "b21": PrinterModel("b21", 384, 5),
    "d11": PrinterModel("d11", 96, 3),
    "d110": PrinterModel("d110", 96, 3),
}


@dataclass
class ImageOptions:
    rotate: int = 0  # clockwise degrees: 0, 90, 180, 270
    label_width_mm: float | None = None  # across the print head
    label_height_mm: float | None = None  # along the feed direction
    fit: FitMode = "contain"
    dither: bool = True
    threshold: int = 128  # used when dither is False
    invert: bool = False


def prepare_image(
    image: Image.Image, model: PrinterModel, opts: ImageOptions
) -> Image.Image:
    """Return a mode "1" image ready for PrinterClient.print_image()."""
    if opts.rotate not in (0, 90, 180, 270):
        raise ValueError("rotate must be one of 0, 90, 180, 270")

    image = ImageOps.exif_transpose(image)
    image = _flatten(image)
    if opts.rotate:
        # PIL rotates counter-clockwise
        image = image.rotate(-opts.rotate, expand=True, fillcolor=255)

    target = _target_size(image, model, opts)
    if target != image.size:
        image = _fit(image, target, opts.fit)

    if image.width > model.max_width_px:
        raise ValueError(
            f"Image is {image.width}px wide but {model.name.upper()} prints at most "
            f"{model.max_width_px}px; use fit/label size or rotate the image"
        )

    if opts.invert:
        image = ImageOps.invert(image)
    if opts.dither:
        return image.convert("1")  # Floyd-Steinberg
    return image.point(lambda p: 255 if p >= opts.threshold else 0).convert("1")


def _flatten(image: Image.Image) -> Image.Image:
    """Grayscale with transparent areas rendered as white (blank label)."""
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(bg, rgba)
    return image.convert("L")


def _target_size(image, model, opts) -> tuple[int, int]:
    w, h = image.size
    if opts.fit == "none":
        return w, h
    if opts.label_width_mm or opts.label_height_mm:
        tw = round(opts.label_width_mm * DOTS_PER_MM) if opts.label_width_mm else None
        th = round(opts.label_height_mm * DOTS_PER_MM) if opts.label_height_mm else None
        if tw is not None:
            tw = min(tw, model.max_width_px)
        if tw is None:
            tw = min(round(w * th / h), model.max_width_px)
        if th is None:
            th = round(h * tw / w)
        return tw, th
    # No label size: only shrink if wider than the print head
    if w > model.max_width_px:
        return model.max_width_px, round(h * model.max_width_px / w)
    return w, h


def _fit(image, size, mode: FitMode) -> Image.Image:
    if mode == "stretch":
        return image.resize(size, Image.LANCZOS)
    if mode == "cover":
        return ImageOps.fit(image, size, Image.LANCZOS)
    # contain: scale to fit, pad with white, centered
    return ImageOps.pad(image, size, Image.LANCZOS, color=255)

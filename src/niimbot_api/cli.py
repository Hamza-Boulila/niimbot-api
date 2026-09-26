import asyncio
import json
import logging

import click
from PIL import Image

from .imaging import MODELS, ImageOptions
from .service import PrinterConfig, PrinterService, PrintJob

printer_options = [
    click.option("-m", "--model", type=click.Choice(list(MODELS)), envvar="NIIMBOT_MODEL", default="b21", show_default=True),
    click.option("-c", "--conn", type=click.Choice(["usb", "ble", "bluetooth"]), envvar="NIIMBOT_CONN", default="usb", show_default=True, help="usb (serial), ble (macOS/Win/Linux) or bluetooth (Linux RFCOMM)"),
    click.option("-a", "--addr", envvar="NIIMBOT_ADDR", help="Serial port, BLE address/name prefix, or MAC"),
]


def with_printer_options(fn):
    for opt in reversed(printer_options):
        fn = opt(fn)
    return fn


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Debug logging")
def main(verbose):
    """Niimbot label printer API and tools."""
    logging.basicConfig(
        level="DEBUG" if verbose else "INFO",
        format="%(levelname)s | %(name)s - %(message)s",
    )


@main.command()
@click.option("-m", "--model", type=click.Choice(list(MODELS)), help="Printer model")
@click.option("-c", "--conn", type=click.Choice(["usb", "ble", "bluetooth"]), help="Connection type")
@click.option("-a", "--addr", help="Serial port, BLE address/name prefix, or MAC")
@click.option("-d", "--density", type=click.IntRange(1, 5))
@click.option("--host", help="Bind address (0.0.0.0 = whole network)")
@click.option("--port", type=int)
def serve(**overrides):
    """Run the HTTP API server.

    Settings come from NIIMBOT_* env vars / .env (see .env.example); options given
    here override them.
    """
    import uvicorn

    from .app import create_app
    from .config import Settings

    settings = Settings(**{k: v for k, v in overrides.items() if v is not None})
    logging.info(
        f"Printer {settings.model.upper()} via {settings.conn}"
        + (f" ({settings.addr})" if settings.addr else "")
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


@main.command("print")
@with_printer_options
@click.argument("image", type=click.Path(exists=True, dir_okay=False))
@click.option("-d", "--density", type=click.IntRange(1, 5), default=3, show_default=True)
@click.option("-r", "--rotate", type=click.Choice(["0", "90", "180", "270"]), default="0", show_default=True, help="Clockwise")
@click.option("-W", "--label-width", type=float, help="Label width in mm (across print head)")
@click.option("-H", "--label-height", type=float, help="Label height in mm (feed direction)")
@click.option("--fit", type=click.Choice(["contain", "cover", "stretch", "none"]), default="contain", show_default=True)
@click.option("--no-dither", is_flag=True, help="Hard threshold instead of dithering")
@click.option("-n", "--copies", type=click.IntRange(1, 100), default=1, show_default=True)
@click.option("--preview", type=click.Path(dir_okay=False), help="Save the bitmap here instead of printing")
def print_cmd(model, conn, addr, image, density, rotate, label_width, label_height, fit, no_dither, copies, preview):
    """Print an image file."""
    service = PrinterService(PrinterConfig(model, conn, addr, density))
    job = PrintJob(
        image=Image.open(image),
        density=density,
        copies=copies,
        options=ImageOptions(
            rotate=int(rotate),
            label_width_mm=label_width,
            label_height_mm=label_height,
            fit=fit,
            dither=not no_dither,
        ),
    )
    if preview:
        bitmap = service.render(job)
        bitmap.save(preview)
        click.echo(f"Saved {bitmap.width}x{bitmap.height} preview to {preview}")
        return
    click.echo(json.dumps(service.print(job)))


@main.command()
@with_printer_options
def status(model, conn, addr):
    """Show printer info, battery and loaded label."""
    service = PrinterService(PrinterConfig(model, conn, addr))
    click.echo(json.dumps(service.status(), indent=2, default=str))


@main.command()
@click.option("-t", "--timeout", default=5.0, show_default=True)
def scan(timeout):
    """Find nearby Niimbot printers over Bluetooth LE."""
    from .ble import scan as ble_scan

    devices = asyncio.run(ble_scan(timeout))
    if not devices:
        click.echo("No Niimbot printers found (is it switched on and not connected to the phone app?)")
    for d in devices:
        click.echo(f"{d['name']:<20} {d['address']}  rssi={d['rssi']}")


if __name__ == "__main__":
    main()

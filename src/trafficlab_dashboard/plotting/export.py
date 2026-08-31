# pyright: reportUnknownMemberType=false

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from matplotlib.figure import Figure
from PIL import Image

from trafficlab.artifacts.io import fsync_published_artifact
from trafficlab.common.errors import TrafficlabError

type ExportFormat = Literal["png", "svg"]


def export_figure(figure: Figure, destination: Path, format: ExportFormat) -> None:
    _validate_destination(destination, format)
    temporary_path: Path | None = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            animated = tuple(artist for artist in figure.findobj() if artist.get_animated())
            for artist in animated:
                artist.set_animated(False)
            try:
                figure.canvas.draw()
                figure.savefig(stream, format=format)
            finally:
                for artist in animated:
                    artist.set_animated(True)
                if animated:
                    figure.canvas.draw()
            stream.flush()
            os.fsync(stream.fileno())
        assert temporary_path is not None
        persisted = temporary_path.read_bytes()
        _validate_export_bytes(persisted, temporary_path, format)
        try:
            os.link(temporary_path, destination)
        except FileExistsError as error:
            raise TrafficlabError(
                f"export destination already exists: {destination}",
                corrective_action="choose a new export filename instead of overwriting the existing figure",
            ) from error
        except OSError as error:
            raise TrafficlabError(
                f"could not publish exported figure {destination}: {error}",
                corrective_action="verify the export directory is writable and has available space",
            ) from error
        published = True
        fsync_published_artifact(destination, stage="publication", affected_evidence=destination.name)
    except TrafficlabError:
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not export figure to {destination}: {error}",
            corrective_action="verify the export directory is writable and has available space",
        ) from error
    except (ValueError, ElementTree.ParseError) as error:
        raise TrafficlabError(
            f"could not validate exported figure {destination}: {error}",
            corrective_action="report the invalid dashboard export output and retry with a different destination",
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                state = "was published" if published else "was not published"
                raise TrafficlabError(
                    f"exported figure {state} at {destination}, but owned temporary file cleanup failed: {error}",
                    corrective_action="preserve the exported figure and remove the reported temporary file if it is still owned",
                ) from error


def _validate_destination(destination: Path, format: ExportFormat) -> None:
    suffix = destination.suffix.lower()
    expected_suffix = f".{format}"
    if suffix != expected_suffix:
        raise TrafficlabError(
            f"export format {format} requires destination suffix {expected_suffix}: {destination}",
            corrective_action="choose a destination whose suffix matches the requested export format",
        )


def _validate_export_bytes(content: bytes, path: Path, format: ExportFormat) -> None:
    if not content:
        raise ValueError("exported figure is empty")
    if format == "png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"exported PNG has an invalid signature: {path}")
        with Image.open(path) as image:
            if image.width <= 0 or image.height <= 0:
                raise ValueError(f"exported PNG has invalid dimensions: {path}")
        return
    ElementTree.parse(path)

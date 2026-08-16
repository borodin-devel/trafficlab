"""Trafficlab research prototype package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("trafficlab")
except PackageNotFoundError:
    __version__ = "0.1.0"

_PROJECT_NAME = "trafficlab"
_REPOSITORY_URL = "https://github.com/borodin-devel/trafficlab"
USER_AGENT = f"{_PROJECT_NAME}/{__version__} (+{_REPOSITORY_URL})"

__all__ = ["USER_AGENT", "__version__"]

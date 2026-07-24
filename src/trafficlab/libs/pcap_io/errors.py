"""Typed PCAPNG parsing, validation, and writing failures."""


class PcapIoError(Exception):
    """Base class for supported PCAPNG boundary failures."""


class InvalidPcapRecordError(PcapIoError):
    """A public record or configured PCAPNG bound is invalid."""


class UnsupportedLinkTypeError(InvalidPcapRecordError):
    """A PCAPNG interface uses a link type outside supported Ethernet."""


class MalformedPcapngError(PcapIoError):
    """Untrusted PCAPNG bytes violate required supported structure."""


class PcapLimitError(MalformedPcapngError):
    """PCAPNG input, block, or packet count exceeds an explicit limit."""

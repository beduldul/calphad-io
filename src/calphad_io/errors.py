"""Error types raised by :mod:`calphad_io`.

Every error carries enough context to locate the problem in the source file.
Parsing never raises bare :class:`ValueError` or :class:`KeyError`; if you see
one of those, it is a bug in this library.
"""

from __future__ import annotations


class CalphadIOError(Exception):
    """Base class for every error raised by this library."""


class ParseError(CalphadIOError):
    """The file could not be parsed.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong.
    line:
        1-based line number in the source file, if known.
    source:
        Name of the file or buffer being parsed, if known.
    """

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        source: str | None = None,
    ) -> None:
        self.message = message
        self.line = line
        self.source = source
        where = []
        if source is not None:
            where.append(str(source))
        if line is not None:
            where.append(f"line {line}")
        prefix = f"{': '.join(where)}: " if where else ""
        super().__init__(f"{prefix}{message}")


class UnsupportedFeatureError(CalphadIOError):
    """The file uses a construct this library recognises but cannot handle.

    This is deliberately distinct from :class:`ParseError`.  A
    :class:`ParseError` means "I do not understand these bytes"; an
    :class:`UnsupportedFeatureError` means "I understand exactly what this is
    and I am refusing to guess".  Callers that want to skip such files can catch
    this one specifically.
    """

    def __init__(
        self,
        feature: str,
        detail: str = "",
        *,
        line: int | None = None,
    ) -> None:
        self.feature = feature
        self.detail = detail
        self.line = line
        msg = f"unsupported feature: {feature}"
        if detail:
            msg = f"{msg} ({detail})"
        if line is not None:
            msg = f"{msg} at line {line}"
        super().__init__(msg)


class WriteError(CalphadIOError):
    """A database could not be written back out.

    Raised when the in-memory model is internally inconsistent in a way that
    would produce a file no reader could load -- for example an endmember whose
    stoichiometry vector does not match the database's element count.
    """


class ValidationError(CalphadIOError):
    """Raised only by strict validation mode.

    The default :func:`calphad_io.validate` never raises; it returns findings.
    """

"""Tokenisation for ChemSage ``.DAT`` files.

The DAT format is a whitespace-delimited token stream with no quoting and no
line continuation: every logical record is just a sequence of tokens, and a
record's extent is determined purely by how many tokens it consumes.  Line
numbers are tracked only for diagnostics.

This matters for correctness.  A reader that consumes a token *before* deciding
whether it can be converted will silently swallow a token every time a
speculative parse fails, desynchronising the rest of the file.  :meth:`Stream.i_`
and :meth:`Stream.f` therefore only advance the cursor once the conversion has
succeeded.
"""

from __future__ import annotations

import re
from typing import List

from ..errors import ParseError

__all__ = ["Stream", "Token", "tokenize"]

_TOKEN_RE = re.compile(r"\S+")


class Token:
    """A single whitespace-delimited token and the line it came from."""

    __slots__ = ("line", "text")

    def __init__(self, text: str, line: int) -> None:
        self.text = text
        self.line = line

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.text!r}, line={self.line})"


def tokenize(text: str, *, start_line: int = 1) -> List[Token]:
    """Split ``text`` into tokens, recording 1-based line numbers.

    ``start_line`` is the line number of the first line of ``text``.
    """
    tokens: List[Token] = []
    for offset, line in enumerate(text.splitlines()):
        for match in _TOKEN_RE.finditer(line):
            tokens.append(Token(match.group(0), start_line + offset))
    return tokens


class Stream:
    """A cursor over a token list.

    Every accessor that converts a token raises :class:`ParseError` with the
    offending line number and leaves the cursor untouched on failure.
    """

    __slots__ = ("index", "last_line", "source", "tokens")

    def __init__(
        self,
        tokens: List[Token],
        *,
        source: str | None = None,
        start_line: int = 1,
    ) -> None:
        self.tokens = tokens
        self.index = 0
        self.source = source
        self.last_line = tokens[0].line if tokens else start_line

    # -- inspection ------------------------------------------------------------

    def peek(self) -> Token | None:
        """The next token, or ``None`` at end of input."""
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def peek_text(self) -> str | None:
        """The text of the next token, or ``None`` at end of input."""
        token = self.peek()
        return None if token is None else token.text

    def at_end(self) -> bool:
        """True when every token has been consumed."""
        return self.index >= len(self.tokens)

    def position(self) -> int:
        """Index of the next unconsumed token."""
        return self.index

    def remaining(self) -> int:
        """How many tokens are left."""
        return len(self.tokens) - self.index

    def line(self) -> int:
        """Line number of the last successfully consumed token."""
        return self.last_line

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Stream(pos={self.index}/{len(self.tokens)}, line={self.last_line})"

    # -- consumption -----------------------------------------------------------

    def next(self) -> Token:
        """Consume and return the next token."""
        if self.index >= len(self.tokens):
            raise ParseError(
                "unexpected end of file",
                line=self.last_line,
                source=self.source,
            )
        token = self.tokens[self.index]
        self.index += 1
        self.last_line = token.line
        return token

    def string(self) -> str:
        """Consume a token and return it as text."""
        return self.next().text

    def integer(self) -> int:
        """Consume a token and convert it to ``int``.

        The cursor does **not** advance if the conversion fails.
        """
        token = self.peek()
        if token is None:
            raise ParseError(
                "unexpected end of file, expected an integer",
                line=self.last_line,
                source=self.source,
            )
        try:
            value = int(token.text)
        except ValueError as exc:
            raise ParseError(
                f"expected an integer, got {token.text!r}",
                line=token.line,
                source=self.source,
            ) from exc
        self.next()
        return value

    def number(self) -> float:
        """Consume a token and convert it to ``float``.

        The cursor does **not** advance if the conversion fails.  Fortran-style
        ``D`` exponents and leading ``+`` are accepted.
        """
        token = self.peek()
        if token is None:
            raise ParseError(
                "unexpected end of file, expected a number",
                line=self.last_line,
                source=self.source,
            )
        cleaned = token.text.replace("D", "E").replace("d", "e")
        try:
            value = float(cleaned)
        except ValueError as exc:
            raise ParseError(
                f"expected a number, got {token.text!r}",
                line=token.line,
                source=self.source,
            ) from exc
        self.next()
        return value

    def numbers(self, count: int) -> List[float]:
        """Consume ``count`` numbers."""
        return [self.number() for _ in range(count)]

    def integers(self, count: int) -> List[int]:
        """Consume ``count`` integers."""
        return [self.integer() for _ in range(count)]

    def strings(self, count: int) -> List[str]:
        """Consume ``count`` tokens as text."""
        return [self.string() for _ in range(count)]

    def skip_line(self) -> None:
        """Consume the rest of the line the cursor is currently on."""
        line = self.last_line
        while self.index < len(self.tokens) and self.tokens[self.index].line == line:
            self.index += 1

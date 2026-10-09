"""Diagram syntax safety.

Component names, technologies and responsibilities come from the caller. They are
rendered into Mermaid and PlantUML source, and both languages have line-oriented,
quote-delimited grammars in which a stray quote, newline or arrow can terminate a
statement early and inject a diagram directive.

The defence is structural, not a blocklist of "dangerous" characters:

* **Node identifiers are never derived from input.** Each component is assigned a
  generated alias (``c1``, ``c2``, …). Input never reaches an identifier position,
  so it cannot influence structure regardless of its content.
* **Labels are sanitised to a conservative character set** and any modification is
  reported as a diagram issue, so a lossy render is visible rather than silent.

That combination means a hostile component name can at worst produce a visibly
altered label. It cannot add a node, an edge, a sub-diagram or a directive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_MAX_LABEL_CHARS: Final = 80
_UNSAFE_CHARS: Final = re.compile(r"[^\w .:/+=@-]", re.UNICODE)
_WHITESPACE: Final = re.compile(r"\s+")
_IDENTIFIER: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class SanitizedLabel:
    """A label after sanitisation, with the fact that it changed."""

    value: str
    modified: bool
    original_length: int


def sanitize_label(text: str, *, max_chars: int = _MAX_LABEL_CHARS) -> SanitizedLabel:
    r"""Reduce ``text`` to characters that cannot alter diagram structure.

    Everything outside ``[\\w .:/+=@-]`` is replaced with a space. That set is
    deliberately narrow: it excludes quotes, backslashes, brackets, angle brackets,
    semicolons, hashes and every arrow form, which are the characters that open or
    terminate a statement in both target languages.
    """
    original_length = len(text)
    replaced = _UNSAFE_CHARS.sub(" ", text)
    collapsed = _WHITESPACE.sub(" ", replaced).strip()
    truncated = collapsed[:max_chars].rstrip()

    if not truncated:
        truncated = "unnamed"

    return SanitizedLabel(
        value=truncated,
        modified=truncated != text.strip(),
        original_length=original_length,
    )


@dataclass(frozen=True, slots=True)
class IdentifierMap:
    """Assigns stable, syntax-safe aliases to named entities.

    Aliases are positional rather than derived from names, which is what makes
    identifier injection structurally impossible.
    """

    aliases: dict[str, str]

    @classmethod
    def assign(cls, names: list[str], *, prefix: str = "c") -> IdentifierMap:
        """Assign an alias to each name, preserving first-seen order."""
        aliases: dict[str, str] = {}
        for position, name in enumerate(names, start=1):
            if name not in aliases:
                aliases[name] = f"{prefix}{position}"
        return cls(aliases=aliases)

    def alias(self, name: str) -> str:
        """Return the alias for ``name``.

        Raises:
            KeyError: if ``name`` was not registered. Rendering an undeclared
                entity would emit a dangling reference, so this is a bug guard
                rather than a validation path.
        """
        return self.aliases[name]

    def __contains__(self, name: object) -> bool:
        """Whether ``name`` has an assigned alias."""
        return name in self.aliases

    @property
    def names(self) -> tuple[str, ...]:
        """Registered names in assignment order."""
        return tuple(self.aliases)


def is_valid_identifier(alias: str) -> bool:
    """Whether ``alias`` is safe to emit into a diagram without quoting."""
    return bool(_IDENTIFIER.match(alias))

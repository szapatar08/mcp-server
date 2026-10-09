"""Structural validation of generated diagram source.

This is a linter, not a parser. It checks the properties a renderer bug actually
breaks:

* the source is empty;
* the first statement is the language's expected directive;
* quotes and brackets balance, because both languages are line-oriented and
  quote-delimited, so one stray quote terminates a statement early;
* identifiers referenced by edges are declared somewhere.

Dangling-reference findings are **warnings**, not errors. An unrecognised
construct is more likely to be a false positive than a truncated diagram, and a
linter that blocks valid output is worse than one that complains. Only the four
checks above can fail a render outright.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from architecture_agent.domain.diagram import DiagramIssue, ValidationSeverity

_QUOTE = '"'


@dataclass(frozen=True, slots=True)
class SyntaxProfile:
    """The constructs one diagram language uses."""

    name: str
    directives: frozenset[str]
    declarations: tuple[re.Pattern[str], ...]
    references: tuple[re.Pattern[str], ...]


_MERMAID_DIRECTIVES: frozenset[str] = frozenset(
    {
        "C4Context",
        "C4Container",
        "C4Component",
        "C4Code",
        "classDiagram",
        "sequenceDiagram",
        "erDiagram",
        "stateDiagram-v2",
        "activityDiagram",
        "graph",
        "flowchart",
    }
)
_PLANTUML_DIRECTIVES: frozenset[str] = frozenset({"@startuml"})

_MERMAID_DECLARATIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:participant|actor)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"\b(?:Container|Component|System|System_Ext|ContainerDb)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,"
    ),
    re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*)\s*[\[\(\{]"),
    re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\[\{:]"),
)
_MERMAID_REFERENCES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bRel\(\s*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\bRel\([^)]*?,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,"),
    re.compile(r"(?:-->|-.->|--x|-x)\s*([A-Za-z_][A-Za-z0-9_]*)"),
)

_PLANTUML_DECLARATIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\{|$|\n)"),
    re.compile(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"\b(?:Component|Container|Container_Boundary|Component_Boundary|Person_Ext|"
        r"System_Ext)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,"
    ),
    re.compile(
        r"^\s*(?:class|interface|abstract|enum|rectangle|actor|participant)\s+"
        r"(?:\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_]*)\s+as\s+([A-Za-z_][A-Za-z0-9_]*)"
    ),
)
_PLANTUML_REFERENCES: tuple[re.Pattern[str], ...] = (
    re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:-->|-\.\.>|->|->>|<\.\.)"),
    re.compile(r"->\s*([A-Za-z_][A-Za-z0-9_]*)"),
)

MERMAID: SyntaxProfile = SyntaxProfile(
    name="mermaid",
    directives=_MERMAID_DIRECTIVES,
    declarations=_MERMAID_DECLARATIONS,
    references=_MERMAID_REFERENCES,
)

PLANTUML: SyntaxProfile = SyntaxProfile(
    name="plantuml",
    directives=_PLANTUML_DIRECTIVES,
    declarations=_PLANTUML_DECLARATIONS,
    references=_PLANTUML_REFERENCES,
)


def find_issues(source: str, profile: SyntaxProfile) -> tuple[DiagramIssue, ...]:
    """Return every structural problem found in ``source``."""
    lines = source.splitlines()

    if not any(line.strip() for line in lines):
        return (DiagramIssue(line=1, message="diagram source is empty"),)

    issues: list[DiagramIssue] = []
    depth = 0
    first = next((line.strip() for line in lines if line.strip()), "")
    if first.split(maxsplit=1)[0] not in profile.directives:
        issues.append(
            DiagramIssue(
                line=1,
                message=f"expected a {profile.name} directive, found {first[:40]!r}",
            )
        )

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.count(_QUOTE) % 2 != 0:
            issues.append(DiagramIssue(line=number, message="unbalanced double quote"))

        if line.rstrip() != line:
            issues.append(
                DiagramIssue(
                    line=number,
                    message="trailing whitespace",
                    severity=ValidationSeverity.WARNING,
                )
            )

        depth, _ = _bracket_depth(stripped, depth)
        if depth < 0:
            issues.append(
                DiagramIssue(line=number, message="closing bracket with no opening bracket")
            )
            depth = 0

    if depth != 0:
        issues.append(
            DiagramIssue(
                line=max(len(lines), 1),
                message=f"unclosed bracket block: {depth} block(s) never closed",
            )
        )

    issues.extend(_undeclared_references(lines, profile))
    return tuple(issues)


def _bracket_depth(line: str, depth: int) -> tuple[int, int]:
    """Advance bracket depth past ``line``.

    Brackets inside quoted strings and ``%%`` comments are ignored, because both
    languages use braces freely inside labels. Depth is tracked across lines
    because PlantUML opens a block on one line and closes it on the next.

    Returns:
        The new depth, and the change this line contributed.
    """
    if line.startswith("%%"):
        return depth, 0

    unquoted = re.sub(r'"[^"]*"', "", line)
    change = (
        unquoted.count("[")
        - unquoted.count("]")
        + unquoted.count("(")
        - unquoted.count(")")
        + unquoted.count("{")
        - unquoted.count("}")
    )
    return depth + change, change


def _undeclared_references(
    lines: Sequence[str], profile: SyntaxProfile
) -> tuple[DiagramIssue, ...]:
    """Report references to identifiers that were never declared."""
    declared: set[str] = set()
    for line in lines:
        for pattern in profile.declarations:
            declared.update(pattern.findall(line))

    if not declared:
        return ()

    referenced: dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        for pattern in profile.references:
            for alias in pattern.findall(stripped):
                referenced.setdefault(alias, number)

    return tuple(
        DiagramIssue(
            line=line_number,
            message=f"identifier {alias!r} is referenced but never declared",
            severity=ValidationSeverity.WARNING,
        )
        for alias, line_number in sorted(referenced.items())
        if alias not in declared
    )

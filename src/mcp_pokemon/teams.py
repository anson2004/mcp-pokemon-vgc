"""Showdown team-paste parser (Pokepaste text) → Team model. Pure, no I/O."""

from __future__ import annotations

import re
import unicodedata

from .models import Spread, SpreadScale, StatKey, Team, TeamMember

STAT_KEYS: tuple[StatKey, ...] = ("hp", "atk", "def", "spa", "spd", "spe")

# to_id(label) -> stat key. Showdown writes "HP / Atk / Def / SpA / SpD / Spe".
STAT_LABELS: dict[str, StatKey] = {
    "hp": "hp",
    "atk": "atk",
    "attack": "atk",
    "def": "def",
    "defense": "def",
    "spa": "spa",
    "spatk": "spa",
    "spd": "spd",
    "spdef": "spd",
    "spe": "spe",
    "speed": "spe",
}

_HEADER_PREFIXES = (
    "ability:",
    "level:",
    "tera type:",
    "evs:",
    "stat points:",
    "ivs:",
    "nature:",
    "shiny:",
    "happiness:",
    "gigantamax:",
    "dynamax level:",
    "hidden power:",
)
_IGNORED_PREFIXES = ("shiny:", "happiness:", "gigantamax:", "dynamax level:", "hidden power:")
_MOVE_BULLETS = ("-", "–", "—")
_GENDER_RE = re.compile(r"\s*\((?:M|F)\)$", re.IGNORECASE)
_NICK_RE = re.compile(r"^(?P<nick>.+?)\s*\((?P<species>[^()]+)\)$")
_STAT_PART_RE = re.compile(r"^(?P<value>-?\d+)\s+(?P<label>.+)$")
_NATURE_LINE_RE = re.compile(r"^(?P<nature>[A-Za-z]+)\s+Nature$", re.IGNORECASE)


class TeamParseError(ValueError):
    """Structural problem in a paste (the message names the line)."""


def to_id(text: str) -> str:
    """Showdown's toID: NFKC, lowercase, keep only [a-z0-9]. Used for moves/items/abilities."""
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", text).lower())


def _err(lineno: int, line: str, msg: str) -> TeamParseError:
    return TeamParseError(f"line {lineno}: {msg}: {line.strip()!r}")


def _is_body_line(line: str) -> bool:
    low = line.lower()
    return low.startswith(_HEADER_PREFIXES) or line.startswith(_MOVE_BULLETS)


def _parse_header(lineno: int, line: str) -> tuple[str, str | None, str | None]:
    """Return (species, nickname, item) from 'Nick (Species) (M) @ Item' and its variants."""
    if _is_body_line(line):
        raise _err(lineno, line, "expected a Pokémon header before this line")
    item: str | None = None
    name = line
    if " @ " in line:
        name, _, item_text = line.rpartition(" @ ")
        item = item_text.strip() or None
    name = _GENDER_RE.sub("", name.strip()).strip()
    m = _NICK_RE.match(name)
    if m:
        nickname: str | None = m.group("nick").strip()
        species = m.group("species").strip()
    else:
        nickname, species = None, name
    if not species:
        raise _err(lineno, line, "empty species name")
    return species, nickname, item


def _parse_stats(lineno: int, line: str, text: str, warnings: list[str]) -> dict[StatKey, int]:
    values: dict[StatKey, int] = {}
    for part in text.split("/"):
        part = part.strip()
        if not part:
            continue
        m = _STAT_PART_RE.match(part)
        if not m:
            raise _err(lineno, line, f"cannot parse stat {part!r}")
        label = to_id(m.group("label"))
        key = STAT_LABELS.get(label)
        if key is None:
            warnings.append(f"line {lineno}: unknown stat label {m.group('label')!r} ignored")
            continue
        if key in values:
            warnings.append(f"line {lineno}: duplicate stat {key}; last value wins")
        values[key] = int(m.group("value"))
    return values


def _resolve_scale(
    lineno: int, values: dict[StatKey, int], hint: SpreadScale | None, warnings: list[str]
) -> SpreadScale:
    top = max(values.values(), default=0)
    if hint == "points" and top > 32:
        warnings.append(f"line {lineno}: value {top} exceeds 32; treating spread as EVs")
        return "ev"
    return hint or "ev"


def _blocks(text: str) -> list[list[tuple[int, str]]]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((lineno, line.strip()))
    if current:
        blocks.append(current)
    return blocks


def _parse_member(
    block: list[tuple[int, str]], scale: SpreadScale | None, warnings: list[str]
) -> TeamMember:
    lineno, header = block[0]
    species, nickname, item = _parse_header(lineno, header)
    ability = tera = nature = None
    level = 50
    moves: list[str] = []
    spread: Spread | None = None
    for lineno, line in block[1:]:
        low = line.lower()
        if low.startswith("ability:"):
            ability = line[len("ability:") :].strip() or None
        elif low.startswith("level:"):
            try:
                level = int(line[len("level:") :].strip())
            except ValueError:
                raise _err(lineno, line, "level must be an integer") from None
            if not 1 <= level <= 100:
                raise _err(lineno, line, "level must be 1–100")
        elif low.startswith("tera type:"):
            tera = line[len("tera type:") :].strip() or None
        elif low.startswith(("evs:", "stat points:")):
            body = line.split(":", 1)[1]
            values = _parse_stats(lineno, line, body, warnings)
            if values:
                spread = Spread(
                    scale=_resolve_scale(lineno, values, scale, warnings), values=values
                )
        elif low.startswith("ivs:"):
            _parse_stats(lineno, line, line.split(":", 1)[1], [])  # syntax only
            note = "IVs ignored (no IVs in this model)"
            if note not in warnings:
                warnings.append(note)
        elif low.startswith("nature:"):
            nature = line[len("nature:") :].strip() or None
        elif (m := _NATURE_LINE_RE.match(line)) is not None:
            nature = m.group("nature")
        elif line.startswith(_MOVE_BULLETS):
            move = line[1:].strip()
            if not move:
                continue
            if len(moves) >= 4:
                raise _err(lineno, line, "more than four moves")
            moves.append(move)
        elif low.startswith(_IGNORED_PREFIXES):
            continue
        else:
            warnings.append(f"line {lineno} ignored: {line!r}")
    return TeamMember(
        species=species,
        nickname=nickname,
        item=item,
        ability=ability,
        tera=tera,
        nature=nature,
        level=level,
        moves=moves,
        spread=spread,
    )


def parse_team(text: str, *, scale: SpreadScale | None = None) -> Team:
    """Parse a Showdown paste into a Team.

    `scale` is the spread scale implied by the format ('points' for Champions); values above 32
    force 'ev' with a warning. Structural problems raise TeamParseError; everything else is
    recorded in Team.warnings.
    """
    blocks = _blocks(text)
    if not blocks:
        raise TeamParseError("empty team paste")
    if len(blocks) > 6:
        raise _err(blocks[6][0][0], blocks[6][0][1], "more than six Pokémon")
    warnings: list[str] = []
    members = [_parse_member(b, scale, warnings) for b in blocks]
    return Team(members=members, warnings=warnings)

"""Pure matchup scoring over a Smogon chaos file and parsed teams. No I/O, no Deps.

Every component is antisymmetric: swapping the two teams negates it. Positive favours team A.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .models import MatchupEdge, SpeedSummary, Team, TeamMember
from .names import NameIndex, fold
from .smogon import ChaosFile, SmogonClient, UsageNotFound, parse_check
from .teams import TeamParseError, to_id

PLUS_SPEED = {"timid", "jolly", "hasty", "naive"}
MINUS_SPEED = {"brave", "relaxed", "quiet", "sassy"}
TRICK_ROOM = "trickroom"
SPEED_CONTROL = {"tailwind", "icywind", "electroweb", "bulldoze", "thunderwave", TRICK_ROOM}
# Champions stat points (0–32) mapped linearly onto the EV scale (0–252). Assumption until the
# in-game formula is verified; only effective_speed depends on it.
POINTS_TO_EV = 252 / 32
IV = 31
LEVEL = 50


# ---- resolution -------------------------------------------------------------------------


def _entry(chaos: ChaosFile, key: str) -> dict[str, Any]:
    return chaos.data[key]


def _weight_total(entry: dict[str, Any]) -> float:
    return float(sum(entry.get("Abilities", {}).values()) or entry.get("Raw count", 0) or 0)


def _key(member: TeamMember) -> str:
    if member.key is None:
        raise ValueError(f"{member.species!r} has not been resolved; call resolve_team first")
    return member.key


def _resolve_brought(chaos: ChaosFile, name: str, keys: list[str], index: NameIndex | None) -> str:
    """Map a 'brought' name onto one of the team's keys (nicknames and short forms allowed)."""
    try:
        key = SmogonClient.resolve_key(chaos, name, index)
    except UsageNotFound:
        key = None
    if key in keys:
        return key
    q = fold(name)
    hits = [k for k in keys if fold(k).startswith(q)]
    if len(hits) == 1:
        return hits[0]
    raise TeamParseError(f"brought Pokémon {name!r} is not on the team ({', '.join(keys)})")


def resolve_team(chaos: ChaosFile, team: Team, index: NameIndex | None = None) -> Team:
    """Fill `key` for each member via the usage-file resolver; raises UsageNotFound."""
    warnings = list(team.warnings)
    members: list[TeamMember] = []
    for m in team.members:
        key = SmogonClient.resolve_key(chaos, m.species, index)
        if not _entry(chaos, key).get("Checks and Counters"):
            warnings.append(f"{key}: no checks/counters data in this usage file")
        members.append(m.model_copy(update={"key": key}))
    keys = [_key(m) for m in members]
    brought: list[str] | None = None
    if team.brought is not None:
        brought = [_resolve_brought(chaos, b, keys, index) for b in team.brought]
        if len(set(brought)) != len(brought):
            raise TeamParseError("brought list contains a duplicate")
    return Team(members=members, brought=brought, warnings=warnings)


# ---- matchup ---------------------------------------------------------------------------


def check_score(chaos: ChaosFile, attacker: str, target: str) -> float | None:
    """Smogon check score (p − 4·d) of `attacker` against `target`, or None if not recorded."""
    cc = parse_check(attacker, _entry(chaos, target).get("Checks and Counters", {}).get(attacker))
    return None if cc is None else cc.score


def matchup_matrix(
    chaos: ChaosFile, keys_a: Iterable[str], keys_b: Iterable[str]
) -> dict[tuple[str, str], MatchupEdge]:
    matrix: dict[tuple[str, str], MatchupEdge] = {}
    keys_b = list(keys_b)
    for a in keys_a:
        for b in keys_b:
            ab = check_score(chaos, a, b)
            ba = check_score(chaos, b, a)
            matrix[(a, b)] = MatchupEdge(
                a=a,
                b=b,
                edge=round((ab or 0.0) - (ba or 0.0), 4),
                a_checks_b=ab,
                b_checks_a=ba,
                no_data=ab is None and ba is None,
            )
    return matrix


def matchup_component(
    matrix: dict[tuple[str, str], MatchupEdge], bring_a: Sequence[str], bring_b: Sequence[str]
) -> float:
    pairs = [matrix[(a, b)].edge for a in bring_a for b in bring_b]
    return sum(pairs) / len(pairs) if pairs else 0.0


# ---- speed -----------------------------------------------------------------------------


def effective_speed(base: int, member: TeamMember) -> int:
    """Level-50 speed stat with 31 IVs, the member's spread and nature."""
    ev = 0
    if member.spread is not None and "spe" in member.spread.values:
        v = member.spread.values["spe"]
        ev = v if member.spread.scale == "ev" else round(v * POINTS_TO_EV)
    stat = (2 * base + IV + ev // 4) * LEVEL // 100 + 5
    nature = to_id(member.nature or "")
    if nature in PLUS_SPEED:
        return stat * 11 // 10
    if nature in MINUS_SPEED:
        return stat * 9 // 10
    return stat


def speed_control_moves(members: Iterable[TeamMember]) -> list[str]:
    seen: dict[str, str] = {}
    for m in members:
        for move in m.moves:
            mid = to_id(move)
            if mid in SPEED_CONTROL and mid not in seen:
                seen[mid] = move
    return list(seen.values())


def has_trick_room(members: Iterable[TeamMember]) -> bool:
    return any(to_id(mv) == TRICK_ROOM for m in members for mv in m.moves)


def speed_summary(
    bring_a: Sequence[TeamMember], bring_b: Sequence[TeamMember], base_speed: dict[str, int]
) -> tuple[SpeedSummary, float]:
    """Pairwise speed comparison over the brought Pokémon; component ∈ [−1, 1]."""

    def speed(m: TeamMember) -> int | None:
        base = base_speed.get(_key(m))
        return None if base is None else effective_speed(base, m)

    sa = [speed(m) for m in bring_a]
    sb = [speed(m) for m in bring_b]
    a_faster = b_faster = ties = 0
    for x in sa:
        for y in sb:
            if x is None or y is None:
                continue
            if x > y:
                a_faster += 1
            elif y > x:
                b_faster += 1
            else:
                ties += 1
    pairs = a_faster + b_faster + ties
    component = (a_faster - b_faster) / pairs if pairs else 0.0
    notes: list[str] = []
    unknown = [
        m.species for m, s in zip([*bring_a, *bring_b], [*sa, *sb], strict=True) if s is None
    ]
    if unknown:
        notes.append(f"base speed unknown for {', '.join(unknown)}")
    if has_trick_room(bring_a) != has_trick_room(bring_b):
        component *= 0.5
        notes.append("speed control contested: one side carries Trick Room")
    summary = SpeedSummary(
        a_faster=a_faster,
        b_faster=b_faster,
        ties=ties,
        a_speed_control=speed_control_moves(bring_a),
        b_speed_control=speed_control_moves(bring_b),
        note="; ".join(notes) or None,
    )
    return summary, round(component, 4)


# ---- cohesion and familiarity ---------------------------------------------------------------


def cohesion(chaos: ChaosFile, keys: Sequence[str]) -> float:
    """Mean teammate rate over ordered pairs within a team (0–1)."""
    rates: list[float] = []
    for a in keys:
        entry = _entry(chaos, a)
        w = _weight_total(entry)
        mates = entry.get("Teammates", {})
        for b in keys:
            if a != b:
                rates.append(float(mates.get(b, 0.0)) / w if w else 0.0)
    return round(sum(rates) / len(rates), 4) if rates else 0.0


def familiarity(chaos: ChaosFile, member: TeamMember) -> float | None:
    """How common this set is in the usage data: mean of item/ability/move shares (0–1)."""
    entry = _entry(chaos, _key(member))
    w = _weight_total(entry)
    parts: list[float] = []
    if member.item:
        items = entry.get("Items", {})
        total = float(sum(items.values()))
        parts.append(float(items.get(to_id(member.item), 0.0)) / total if total else 0.0)
    if member.ability:
        parts.append(
            float(entry.get("Abilities", {}).get(to_id(member.ability), 0.0)) / w if w else 0.0
        )
    moves = entry.get("Moves", {})
    for move in member.moves:
        parts.append(float(moves.get(to_id(move), 0.0)) / w if w else 0.0)
    return round(sum(parts) / len(parts), 4) if parts else None


def team_familiarity(chaos: ChaosFile, members: Iterable[TeamMember]) -> float:
    vals = [f for f in (familiarity(chaos, m) for m in members) if f is not None]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


# ---- components --------------------------------------------------------------------------


@dataclass
class Components:
    values: dict[str, float]  # matchup, speed, cohesion, familiarity — positive favours A
    matrix: dict[tuple[str, str], MatchupEdge]
    speed: SpeedSummary


def score_components(
    chaos: ChaosFile,
    bring_a: Sequence[TeamMember],
    bring_b: Sequence[TeamMember],
    base_speed: dict[str, int],
) -> Components:
    """Signed feature vector for a brought-four vs brought-four matchup."""
    keys_a = [_key(m) for m in bring_a]
    keys_b = [_key(m) for m in bring_b]
    matrix = matchup_matrix(chaos, keys_a, keys_b)
    speed, speed_component = speed_summary(bring_a, bring_b, base_speed)
    values = {
        "matchup": round(matchup_component(matrix, keys_a, keys_b), 4),
        "speed": speed_component,
        "cohesion": round(cohesion(chaos, keys_a) - cohesion(chaos, keys_b), 4),
        "familiarity": round(
            team_familiarity(chaos, bring_a) - team_familiarity(chaos, bring_b), 4
        ),
    }
    return Components(values=values, matrix=matrix, speed=speed)

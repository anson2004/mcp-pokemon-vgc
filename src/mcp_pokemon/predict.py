"""Pure matchup scoring over a Smogon chaos file and parsed teams. No I/O, no Deps.

Every component is antisymmetric: swapping the two teams negates it. Positive favours team A.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from .models import (
    BattlePrediction,
    BringPlan,
    BringSource,
    EdgeBasis,
    MatchupEdge,
    SpeedSummary,
    Team,
    TeamMember,
    Threat,
)
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

# Logistic weights over the signed components. Hand-picked for stage one (see PLAN.md phase 3):
# a mean check edge is small (typically within ±0.15) so it gets the largest weight; speed is
# already in [−1, 1]. Stage two fits these from replays.
WEIGHTS: dict[str, float] = {"matchup": 4.0, "speed": 0.4, "cohesion": 0.3, "familiarity": 0.2}
BRING_SIZE = 4
KEY_MATCHUPS = 5

# Type-chart fallback for pairs without checks/counters data: one step of effectiveness
# (2× vs neutral) counts as roughly one typical check edge.
TYPE_EDGE_SCALE = 0.05
TypeChart = dict[str, dict[str, float]]  # attacking type -> defending type -> multiplier ≠ 1
TypeEdge = Callable[[str, str], float | None]


# ---- resolution -------------------------------------------------------------------------


def _entry(chaos: ChaosFile, key: str) -> dict[str, Any]:
    return chaos.data[key]


def _weight_total(entry: dict[str, Any]) -> float:
    return float(sum(entry.get("Abilities", {}).values()) or entry.get("Raw count", 0) or 0)


def _key(member: TeamMember) -> str:
    if member.key is None:
        raise ValueError(f"{member.species!r} has not been resolved; call resolve_team first")
    return member.key


def _resolve_brought(
    chaos: ChaosFile, name: str, members: Sequence[TeamMember], index: NameIndex | None
) -> str:
    """Map a 'brought' name onto one of the team's keys.

    Accepts the paste nickname, any name resolve_key understands, or a unique key prefix.
    """
    keys = [_key(m) for m in members]
    for m in members:
        if m.nickname and fold(m.nickname) == fold(name):
            return _key(m)
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
    brought: list[str] | None = None
    if team.brought is not None:
        brought = [_resolve_brought(chaos, b, members, index) for b in team.brought]
        if len(set(brought)) != len(brought):
            raise TeamParseError("brought list contains a duplicate")
    return Team(members=members, brought=brought, warnings=warnings)


# ---- matchup ---------------------------------------------------------------------------


def check_score(chaos: ChaosFile, attacker: str, target: str) -> float | None:
    """Smogon check score (p − 4·d) of `attacker` against `target`, or None if not recorded."""
    cc = parse_check(attacker, _entry(chaos, target).get("Checks and Counters", {}).get(attacker))
    return None if cc is None else cc.score


def type_effectiveness(chart: TypeChart, attacking: str, defending: Sequence[str]) -> float:
    mult = 1.0
    for d in defending:
        mult *= chart.get(attacking, {}).get(d, 1.0)
    return mult


def _log_eff(mult: float) -> float:
    return -2.0 if mult == 0 else math.log2(mult)


def type_edge_fn(types: Mapping[str, Sequence[str]], chart: TypeChart) -> TypeEdge:
    """Edge estimate from typing alone: best STAB effectiveness of a on b minus b on a.

    Antisymmetric by construction. Returns None when either side's typing is unknown.
    """

    def best(att: Sequence[str], dfn: Sequence[str]) -> float:
        return max(_log_eff(type_effectiveness(chart, t, dfn)) for t in att)

    def edge(a: str, b: str) -> float | None:
        ta, tb = types.get(a), types.get(b)
        if not ta or not tb:
            return None
        return TYPE_EDGE_SCALE * (best(ta, tb) - best(tb, ta))

    return edge


def matchup_matrix(
    chaos: ChaosFile,
    keys_a: Iterable[str],
    keys_b: Iterable[str],
    type_edge: TypeEdge | None = None,
) -> dict[tuple[str, str], MatchupEdge]:
    """Pairwise edges; pairs with no check data fall back to `type_edge` when given, else 0."""
    matrix: dict[tuple[str, str], MatchupEdge] = {}
    keys_b = list(keys_b)
    for a in keys_a:
        for b in keys_b:
            ab = check_score(chaos, a, b)
            ba = check_score(chaos, b, a)
            no_data = ab is None and ba is None
            edge = (ab or 0.0) - (ba or 0.0)
            basis: EdgeBasis = "checks"
            if no_data:
                est = type_edge(a, b) if type_edge else None
                edge, basis = (est, "types") if est is not None else (0.0, "none")
            matrix[(a, b)] = MatchupEdge(
                a=a,
                b=b,
                edge=round(edge, 4),
                a_checks_b=ab,
                b_checks_a=ba,
                no_data=no_data,
                basis=basis,
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
    type_edge: TypeEdge | None = None,
) -> Components:
    """Signed feature vector for a brought-four vs brought-four matchup."""
    keys_a = [_key(m) for m in bring_a]
    keys_b = [_key(m) for m in bring_b]
    matrix = matchup_matrix(chaos, keys_a, keys_b, type_edge)
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


# ---- bring selection and prediction ----------------------------------------------------------


def _candidate_brings(team: Team) -> list[list[TeamMember]]:
    if team.brought:
        by_key = {_key(m): m for m in team.members}
        return [[by_key[k] for k in team.brought]]
    n = min(BRING_SIZE, len(team.members))
    return [list(c) for c in combinations(team.members, n)]


def _selection_score(values: dict[str, float]) -> float:
    return WEIGHTS["matchup"] * values["matchup"] + WEIGHTS["speed"] * values["speed"]


def choose_bring(
    chaos: ChaosFile,
    team_a: Team,
    team_b: Team,
    base_speed: dict[str, int],
    type_edge: TypeEdge | None = None,
) -> tuple[list[TeamMember], list[TeamMember], BringPlan, BringPlan]:
    """Pick each side's four.

    A given `brought` list is used as is. Otherwise each side independently plays maximin on the
    matchup + speed subscore over its 4-of-6 subsets (a best response when the other side is
    given). Both sides use the same rule, so swapping the teams swaps the answer and a mirror
    match picks the same four on both sides.
    """
    subs_a, subs_b = _candidate_brings(team_a), _candidate_brings(team_b)
    table = {
        (i, j): _selection_score(score_components(chaos, sa, sb, base_speed, type_edge).values)
        for i, sa in enumerate(subs_a)
        for j, sb in enumerate(subs_b)
    }
    ia = max(range(len(subs_a)), key=lambda i: min(table[(i, j)] for j in range(len(subs_b))))
    jb = max(range(len(subs_b)), key=lambda j: min(-table[(i, j)] for i in range(len(subs_a))))

    def source(own: Team, other: Team) -> BringSource:
        if own.brought:
            return "given"
        return "best_response" if other.brought else "minimax"

    plan_a = BringPlan(pokemon=[_key(m) for m in subs_a[ia]], source=source(team_a, team_b))
    plan_b = BringPlan(pokemon=[_key(m) for m in subs_b[jb]], source=source(team_b, team_a))
    return subs_a[ia], subs_b[jb], plan_a, plan_b


def win_probability(values: dict[str, float]) -> float:
    z = sum(WEIGHTS[k] * v for k, v in values.items())
    return 1.0 / (1.0 + math.exp(-z))


def predict(
    chaos: ChaosFile,
    team_a: Team,
    team_b: Team,
    base_speed: dict[str, int],
    type_edge: TypeEdge | None = None,
) -> BattlePrediction:
    """Heuristic win probability for team A with the facts behind it. Teams must be resolved."""
    bring_a, bring_b, plan_a, plan_b = choose_bring(chaos, team_a, team_b, base_speed, type_edge)
    comps = score_components(chaos, bring_a, bring_b, base_speed, type_edge)
    edges = [e for e in comps.matrix.values() if e.basis != "none"]
    edges.sort(key=lambda e: -abs(e.edge))

    warnings = [f"team A: {w}" for w in team_a.warnings] + [f"team B: {w}" for w in team_b.warnings]
    if comps.speed.note:
        warnings.append(comps.speed.note)
    if "champions" in chaos.format_id and any(m.tera for m in [*team_a.members, *team_b.members]):
        warnings.append("Tera types ignored: Champions formats have no Terastallization")
    if any(e.basis == "types" for e in comps.matrix.values()):
        warnings.append("some pairs have no check data; type chart used as a fallback")

    return BattlePrediction(
        format_id=chaos.format_id,
        rating=chaos.rating,
        month=chaos.month,
        win_probability_a=round(win_probability(comps.values), 3),
        bring_a=plan_a,
        bring_b=plan_b,
        key_matchups=edges[:KEY_MATCHUPS],
        speed=comps.speed,
        components=comps.values,
        warnings=warnings,
    )


def threats(
    chaos: ChaosFile,
    keys: Sequence[str],
    pool: Iterable[str],
    type_edge: TypeEdge | None = None,
    top_n: int = 5,
) -> list[Threat]:
    """Pokémon from `pool` (usually the format's top N) ranked by mean edge against the team."""
    out: list[Threat] = []
    for m in pool:
        if m in keys:
            continue
        edges = list(matchup_matrix(chaos, [m], keys, type_edge).values())
        if not edges or all(e.basis == "none" for e in edges):
            continue
        mean = sum(e.edge for e in edges) / len(edges)
        beats = [e.b for e in sorted(edges, key=lambda e: -e.edge) if e.edge > 0]
        out.append(
            Threat(
                pokemon=m,
                usage_percent=round(100 * chaos.data[m]["usage"], 2),
                edge=round(mean, 4),
                beats=beats,
            )
        )
    out.sort(key=lambda t: -t.edge)
    return out[:top_n]

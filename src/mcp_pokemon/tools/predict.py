"""Battle-prediction tools: parse team pastes, pick fours, estimate a win probability."""

from __future__ import annotations

from typing import Any

from ..deps import Deps
from ..models import BattlePrediction, SpreadScale, Team, TeamReport
from ..names import AmbiguousName, to_pokeapi_slug
from ..pokeapi import PokemonNotFound
from ..predict import (
    TypeChart,
    TypeEdge,
    predict,
    resolve_team,
    speed_control_moves,
    threats,
    type_edge_fn,
)
from ..smogon import ChaosFile
from ..teams import parse_team
from .vgc import _load

THREAT_POOL = 30


def _scale(chaos: ChaosFile) -> SpreadScale:
    return "points" if "champions" in chaos.format_id else "ev"


async def _resolve(
    deps: Deps, chaos: ChaosFile, text: str, brought: list[str] | None = None
) -> Team:
    team = parse_team(text, scale=_scale(chaos))
    if brought is not None:
        team = team.model_copy(update={"brought": brought})
    return resolve_team(chaos, team, await deps.names.get())


async def _pokemon_json(deps: Deps, key: str) -> dict[str, Any] | None:
    """PokéAPI pokemon JSON for a usage key: direct slug first, then the name resolver
    (handles default varieties such as Basculegion → basculegion-male)."""
    pj = await deps.pokeapi.pokemon(to_pokeapi_slug(key))
    if pj is not None:
        return pj
    try:
        _, pj, _ = await deps.pokeapi.resolve(key)
    except (PokemonNotFound, AmbiguousName):
        return None
    return pj


async def _battle_info(deps: Deps, keys: list[str]) -> tuple[dict[str, int], TypeEdge, list[str]]:
    """Base speeds and a type-chart edge function for the given usage keys, from PokéAPI."""
    base_speed: dict[str, int] = {}
    types: dict[str, list[str]] = {}
    type_urls: dict[str, str] = {}
    warnings: list[str] = []
    for key in keys:
        pj = await _pokemon_json(deps, key)
        if pj is None:
            warnings.append(f"{key}: not on PokéAPI; speed and type fallback unavailable")
            continue
        for st in pj["stats"]:
            if st["stat"]["name"] == "speed":
                base_speed[key] = int(st["base_stat"])
        types[key] = [t["type"]["name"] for t in sorted(pj["types"], key=lambda t: t["slot"])]
        for t in pj["types"]:
            type_urls[t["type"]["name"]] = t["type"]["url"]

    chart: TypeChart = {}
    for name, url in type_urls.items():
        tj: dict[str, Any] | None = await deps.pokeapi.type_data(url)
        rel = (tj or {}).get("damage_relations", {})
        row: dict[str, float] = {}
        for mult, field in (
            (2.0, "double_damage_to"),
            (0.5, "half_damage_to"),
            (0.0, "no_damage_to"),
        ):
            for d in rel.get(field, []):
                row[d["name"]] = mult
        chart[name] = row
    return base_speed, type_edge_fn(types, chart), warnings


async def parse_vgc_team(
    deps: Deps,
    team: str,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
) -> Team:
    chaos = await _load(deps, format_id, rating, month)
    return await _resolve(deps, chaos, team)


async def predict_vgc_battle(
    deps: Deps,
    team_a: str,
    team_b: str,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
    brought_a: list[str] | None = None,
    brought_b: list[str] | None = None,
) -> BattlePrediction:
    chaos = await _load(deps, format_id, rating, month)
    a = await _resolve(deps, chaos, team_a, brought_a)
    b = await _resolve(deps, chaos, team_b, brought_b)
    keys = [m.key for m in [*a.members, *b.members] if m.key is not None]
    base_speed, type_edge, warnings = await _battle_info(deps, list(dict.fromkeys(keys)))
    result = predict(chaos, a, b, base_speed, type_edge)
    result.warnings.extend(warnings)
    return result


async def analyze_vgc_team(
    deps: Deps,
    team: str,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
    top_n: int = 5,
) -> TeamReport:
    chaos = await _load(deps, format_id, rating, month)
    t = await _resolve(deps, chaos, team)
    keys = [m.key for m in t.members if m.key is not None]
    pool = chaos.ranking[:THREAT_POOL]
    base_speed, type_edge, warnings = await _battle_info(deps, list(dict.fromkeys([*keys, *pool])))
    return TeamReport(
        format_id=chaos.format_id,
        rating=chaos.rating,
        month=chaos.month,
        pokemon=keys,
        threats=threats(chaos, keys, pool, type_edge, top_n),
        speed_control=speed_control_moves(t.members),
        warnings=[*t.warnings, *warnings],
    )

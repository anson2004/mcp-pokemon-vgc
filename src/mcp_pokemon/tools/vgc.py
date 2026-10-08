"""VGC usage tools backed by Smogon chaos files."""

from __future__ import annotations

from ..deps import Deps
from ..models import UsageRank, VgcComparison, VgcFormat, VgcUsage
from ..smogon import ChaosFile


async def list_vgc_formats(deps: Deps, month: str | None = None) -> list[VgcFormat]:
    return await deps.smogon.formats(month)


async def _load(
    deps: Deps, format_id: str | None, rating: int | None, month: str | None
) -> ChaosFile:
    fmt, rating = await deps.smogon.resolve_format(format_id, rating, month)
    return await deps.smogon.chaos(fmt, rating)


async def top_vgc_usage(
    deps: Deps,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
    limit: int = 30,
) -> list[UsageRank]:
    chaos = await _load(deps, format_id, rating, month)
    return deps.smogon.top(chaos, limit)


async def get_vgc_usage(
    deps: Deps,
    pokemon: str,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
    top_n: int = 5,
) -> VgcUsage:
    chaos = await _load(deps, format_id, rating, month)
    index = await deps.names.get()
    key = deps.smogon.resolve_key(chaos, pokemon, index)
    return deps.smogon.usage(chaos, key, top_n)


async def compare_vgc(
    deps: Deps,
    pokemon_a: str,
    pokemon_b: str,
    format_id: str | None = None,
    rating: int | None = None,
    month: str | None = None,
    top_n: int = 5,
) -> VgcComparison:
    chaos = await _load(deps, format_id, rating, month)
    index = await deps.names.get()
    key_a = deps.smogon.resolve_key(chaos, pokemon_a, index)
    key_b = deps.smogon.resolve_key(chaos, pokemon_b, index)
    return deps.smogon.compare(chaos, key_a, key_b, top_n)

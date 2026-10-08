"""Pokédex tools: search and describe in several languages."""

from __future__ import annotations

from ..deps import Deps
from ..models import DEFAULT_LANGS, Lang, NameResolution, PokemonSummary
from ..names import AmbiguousName


def _langs(langs: list[Lang] | None) -> tuple[Lang, ...]:
    return tuple(langs) if langs else DEFAULT_LANGS


async def resolve_pokemon(
    deps: Deps, query: str, langs: list[Lang] | None = None, limit: int = 8
) -> NameResolution:
    """Nickname / any-language name -> official names. Index only, no PokéAPI calls."""
    index = await deps.names.get()
    chosen = _langs(langs)
    try:
        res = index.resolve(query) or index.resolve_fuzzy(query)
    except AmbiguousName:
        res = None
    if res is not None:
        variety = res.variety
        if variety is None and res.form:
            variety = f"{res.entry.slug}-{res.form}"
        return NameResolution(
            query=query, resolved=index.match(res.entry, chosen, variety), note=res.note
        )
    candidates = [index.match(e, chosen) for e in index.search(query, limit=limit)]
    note = None
    if len(candidates) > 1:
        note = f"'{query}' matches several Pokémon; pick one of the candidates"
    return NameResolution(query=query, resolved=None, note=note, candidates=candidates)


async def describe_pokemon(
    deps: Deps, name: str, langs: list[Lang] | None = None, form: str | None = None
) -> PokemonSummary:
    return await deps.pokeapi.summary(name, _langs(langs), form)

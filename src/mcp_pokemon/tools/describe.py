"""Pokédex tools: search and describe in several languages."""

from __future__ import annotations

from ..deps import Deps
from ..models import DEFAULT_LANGS, Lang, PokemonMatch, PokemonSummary


def _langs(langs: list[Lang] | None) -> tuple[Lang, ...]:
    return tuple(langs) if langs else DEFAULT_LANGS


async def search_pokemon(
    deps: Deps, query: str, langs: list[Lang] | None = None, limit: int = 10
) -> list[PokemonMatch]:
    index = await deps.names.get()
    return [index.match(e, _langs(langs)) for e in index.search(query, limit=limit)]


async def describe_pokemon(
    deps: Deps, name: str, langs: list[Lang] | None = None, form: str | None = None
) -> PokemonSummary:
    return await deps.pokeapi.summary(name, _langs(langs), form)

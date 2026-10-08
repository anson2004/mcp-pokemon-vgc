"""PokéAPI client producing localized PokemonSummary objects."""

from __future__ import annotations

import re
from typing import Any

from .cache import TTL_POKEAPI, Http, TTLCache
from .models import (
    AbilitySummary,
    Lang,
    LocalizedText,
    PokemonSummary,
    StatBlock,
    TypeSummary,
)
from .names import NameIndexLoader, Resolution, to_pokeapi_slug

BASE = "https://pokeapi.co/api/v2/"

STAT_KEYS = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "special_attack",
    "special-defense": "special_defense",
    "speed": "speed",
}


class PokemonNotFound(Exception):
    def __init__(self, query: str, suggestions: list[str]) -> None:
        self.query = query
        self.suggestions = suggestions
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"No Pokémon matches '{query}'.{hint}")


def pick_names(names: list[dict[str, Any]], langs: tuple[Lang, ...]) -> LocalizedText:
    out: LocalizedText = {}
    for n in names:
        lang = n["language"]["name"]
        if lang in langs:
            out[lang] = n["name"]
    return out


def pick_genus(genera: list[dict[str, Any]], langs: tuple[Lang, ...]) -> LocalizedText:
    out: LocalizedText = {}
    for g in genera:
        lang = g["language"]["name"]
        if lang in langs:
            out[lang] = g["genus"]
    return out


def pick_flavor(entries: list[dict[str, Any]], langs: tuple[Lang, ...]) -> LocalizedText:
    """Latest flavor text per language (entries are ordered by version)."""
    out: LocalizedText = {}
    for e in entries:
        lang = e["language"]["name"]
        if lang in langs:
            out[lang] = re.sub(r"\s+", " ", e["flavor_text"]).strip()
    return out


class PokeApiClient:
    def __init__(self, http: Http, cache: TTLCache, names: NameIndexLoader) -> None:
        self._http = http
        self._cache = cache
        self._names = names

    async def _get(self, path_or_url: str) -> dict[str, Any] | None:
        url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url

        async def fetch() -> dict[str, Any] | None:
            resp = await self._http.get(url)
            if resp.status_code in (400, 404):  # PokéAPI answers 400 for non-ASCII slugs
                return None
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        return await self._cache.get_or_fetch("pokeapi:" + url, TTL_POKEAPI, fetch)

    async def species(self, id_or_slug: int | str) -> dict[str, Any] | None:
        return await self._get(f"pokemon-species/{id_or_slug}")

    async def pokemon(self, id_or_slug: int | str) -> dict[str, Any] | None:
        return await self._get(f"pokemon/{id_or_slug}")

    async def resolve(
        self, query: str, form: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any], str | None]:
        """Return (species_json, pokemon_json, note) for a query in any supported language.

        Resolution order:
        1. exact species name in any language, alias, or localized form prefix (超级/メガ...)
        2. a PokéAPI / Showdown variety slug (e.g. 'urshifu-rapid-strike')
        3. 'species-form' split (e.g. 'ogerpon-wellspring')
        4. unique substring of any official name (e.g. 咆哮虎 -> 炽焰咆哮虎)
        """
        index = await self._names.get()
        res: Resolution | None = index.resolve(query)
        slug = to_pokeapi_slug(query)

        if res is None:
            pokemon = await self.pokemon(slug) if slug.isascii() else None
            if pokemon is not None:
                species = await self._get(pokemon["species"]["url"])
                assert species is not None
                return species, pokemon, None

            if "-" in slug and slug.isascii():
                head, _, tail = slug.partition("-")
                head_entry = index.lookup(head)
                if head_entry is not None:
                    species = await self.species(head_entry.species_id)
                    assert species is not None
                    variety = self._choose_variety(species, tail)
                    pokemon = await self.pokemon(variety)
                    if pokemon is not None:
                        return species, pokemon, None

            res = index.resolve_fuzzy(query)  # may raise AmbiguousName
            if res is None:
                raise PokemonNotFound(query, index.suggestions(query))

        species = await self.species(res.entry.species_id)
        assert species is not None
        variety = res.variety or self._choose_variety(species, form or res.form)
        pokemon = await self.pokemon(variety)
        if pokemon is None:
            raise PokemonNotFound(f"{query} ({variety})", [])
        return species, pokemon, res.note

    @staticmethod
    def _choose_variety(species: dict[str, Any], form: str | None) -> str:
        varieties = species["varieties"]
        if form:
            f = to_pokeapi_slug(form)
            species_slug = species["name"]
            wanted = f if f.startswith(species_slug) else f"{species_slug}-{f}"
            for v in varieties:
                if v["pokemon"]["name"] == wanted:
                    return str(v["pokemon"]["name"])
            for v in varieties:
                if f in v["pokemon"]["name"]:
                    return str(v["pokemon"]["name"])
        for v in varieties:
            if v["is_default"]:
                return str(v["pokemon"]["name"])
        return str(varieties[0]["pokemon"]["name"])

    async def summary(
        self, query: str, langs: tuple[Lang, ...], form: str | None = None
    ) -> PokemonSummary:
        species, pokemon, note = await self.resolve(query, form)

        types: list[TypeSummary] = []
        for t in sorted(pokemon["types"], key=lambda t: t["slot"]):
            tj = await self._get(t["type"]["url"])
            assert tj is not None
            types.append(TypeSummary(slug=tj["name"], names=pick_names(tj["names"], langs)))

        abilities: list[AbilitySummary] = []
        for a in sorted(pokemon["abilities"], key=lambda a: a["slot"]):
            aj = await self._get(a["ability"]["url"])
            assert aj is not None
            abilities.append(
                AbilitySummary(
                    slug=aj["name"], names=pick_names(aj["names"], langs), is_hidden=a["is_hidden"]
                )
            )

        stats = {STAT_KEYS[s["stat"]["name"]]: s["base_stat"] for s in pokemon["stats"]}

        names = pick_names(species["names"], langs)
        # Variety-specific localized names (e.g. regional forms) when PokéAPI has them.
        form_names: LocalizedText = {}
        for f in pokemon.get("forms", []):
            fj = await self._get(f["url"])
            if fj and fj.get("names"):
                form_names = pick_names(fj["names"], langs)
        if form_names:
            names = {**names, **form_names}

        return PokemonSummary(
            id=pokemon["id"],
            species_id=species["id"],
            slug=pokemon["name"],
            species_slug=species["name"],
            names=names,
            genus=pick_genus(species["genera"], langs),
            types=types,
            abilities=abilities,
            stats=StatBlock(**stats),
            height_m=pokemon["height"] / 10,
            weight_kg=pokemon["weight"] / 10,
            forms=[v["pokemon"]["name"] for v in species["varieties"]],
            flavor_text=pick_flavor(species["flavor_text_entries"], langs),
            is_legendary=species["is_legendary"],
            is_mythical=species["is_mythical"],
            note=note,
        )

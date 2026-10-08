"""Multilingual name index (built from PokéAPI's source CSV, fetched online) and
Showdown <-> PokéAPI slug normalisation."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

from .cache import TTL_POKEAPI, Http, TTLCache
from .models import Lang, LocalizedText, PokemonMatch

CSV_BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
SPECIES_NAMES_CSV = CSV_BASE + "pokemon_species_names.csv"
SPECIES_CSV = CSV_BASE + "pokemon_species.csv"

# PokéAPI language ids -> our Lang codes (see data/v2/csv/languages.csv)
LANG_BY_ID: dict[int, Lang] = {
    9: "en",
    11: "ja",
    1: "ja-hrkt",
    12: "zh-hans",
    4: "zh-hant",
    3: "ko",
}


def strip_latin_accents(text: str) -> str:
    """Remove accents from Latin letters only (é -> e) while leaving kana dakuten intact."""
    out: list[str] = []
    for ch in text:
        if ord(ch) >= 0x2000:  # CJK, kana, hangul: keep combining marks (dakuten etc.)
            out.append(ch)
        else:
            base = unicodedata.normalize("NFD", ch)
            out.append("".join(c for c in base if unicodedata.category(c) != "Mn"))
    return "".join(out)


def fold(text: str) -> str:
    """Normalise for matching: NFKC, casefold, strip Latin accents, keep only letters/digits."""
    text = strip_latin_accents(unicodedata.normalize("NFKC", text).casefold())
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+", "", text)


def to_pokeapi_slug(name: str) -> str:
    """Convert a Showdown-style or human name to a PokéAPI slug candidate."""
    text = strip_latin_accents(unicodedata.normalize("NFKC", name).strip().lower())
    text = re.sub(r"[.'’:]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return SHOWDOWN_TO_POKEAPI.get(text, text)


# Showdown names whose PokéAPI slug cannot be derived mechanically.
SHOWDOWN_TO_POKEAPI: dict[str, str] = {
    "urshifu": "urshifu-single-strike",
    "ogerpon-wellspring": "ogerpon-wellspring-mask",
    "ogerpon-hearthflame": "ogerpon-hearthflame-mask",
    "ogerpon-cornerstone": "ogerpon-cornerstone-mask",
    "calyrex-ice": "calyrex-ice-rider",
    "calyrex-shadow": "calyrex-shadow-rider",
    "necrozma-dusk-mane": "necrozma-dusk",
    "necrozma-dawn-wings": "necrozma-dawn",
    "tauros-paldea-combat": "tauros-paldea-combat-breed",
    "tauros-paldea-blaze": "tauros-paldea-blaze-breed",
    "tauros-paldea-aqua": "tauros-paldea-aqua-breed",
    "indeedee-f": "indeedee-female",
    "meowstic-f": "meowstic-female",
    "basculegion-f": "basculegion-female",
    "oinkologne-f": "oinkologne-female",
    "zygarde-10%": "zygarde-10",
    "zygarde-10": "zygarde-10",
    "zygarde-complete": "zygarde-complete",
    "greninja-bond": "greninja-battle-bond",
    "terapagos-terastal": "terapagos-terastal",
    "terapagos-stellar": "terapagos-stellar",
    "sinistcha-masterpiece": "sinistcha-masterpiece",
    "poltchageist-artisan": "poltchageist-artisan",
    "rockruff-dusk": "rockruff-own-tempo",
}


@dataclass
class SpeciesEntry:
    species_id: int
    slug: str
    names: LocalizedText = field(default_factory=dict)
    genus: LocalizedText = field(default_factory=dict)


@dataclass
class NameIndex:
    by_id: dict[int, SpeciesEntry]
    by_slug: dict[str, SpeciesEntry]
    exact: dict[str, list[SpeciesEntry]]  # folded name -> entries

    @classmethod
    def from_csv(cls, species_csv: str, names_csv: str) -> NameIndex:
        by_id: dict[int, SpeciesEntry] = {}
        for row in csv.DictReader(io.StringIO(species_csv)):
            sid = int(row["id"])
            by_id[sid] = SpeciesEntry(species_id=sid, slug=row["identifier"])
        for row in csv.DictReader(io.StringIO(names_csv)):
            lang = LANG_BY_ID.get(int(row["local_language_id"]))
            if lang is None:
                continue
            entry = by_id.get(int(row["pokemon_species_id"]))
            if entry is None:
                continue
            entry.names[lang] = row["name"]
            if row.get("genus"):
                entry.genus[lang] = row["genus"]
        exact: dict[str, list[SpeciesEntry]] = {}
        for entry in by_id.values():
            keys = {fold(entry.slug), *(fold(n) for n in entry.names.values())}
            for key in keys:
                if key:
                    exact.setdefault(key, []).append(entry)
        return cls(by_id=by_id, by_slug={e.slug: e for e in by_id.values()}, exact=exact)

    def lookup(self, query: str) -> SpeciesEntry | None:
        hits = self.exact.get(fold(query))
        if hits:
            return hits[0]
        slug = to_pokeapi_slug(query)
        if slug in self.by_slug:
            return self.by_slug[slug]
        return None

    def search(self, query: str, limit: int = 10) -> list[SpeciesEntry]:
        q = fold(query)
        if not q:
            return []
        results: list[SpeciesEntry] = []
        seen: set[int] = set()

        def add(entry: SpeciesEntry) -> None:
            if entry.species_id not in seen:
                seen.add(entry.species_id)
                results.append(entry)

        for e in self.exact.get(q, []):
            add(e)
        for key, entries in self.exact.items():
            if key.startswith(q):
                for e in entries:
                    add(e)
        for key, entries in self.exact.items():
            if q in key:
                for e in entries:
                    add(e)
        return results[:limit]

    def match(self, entry: SpeciesEntry, langs: tuple[Lang, ...]) -> PokemonMatch:
        return PokemonMatch(
            species_id=entry.species_id,
            slug=entry.slug,
            names={lang: entry.names[lang] for lang in langs if lang in entry.names},
        )


class NameIndexLoader:
    def __init__(self, http: Http, cache: TTLCache) -> None:
        self._http = http
        self._cache = cache

    async def get(self) -> NameIndex:
        async def build() -> NameIndex:
            species = await self._http.get_text(SPECIES_CSV)
            names = await self._http.get_text(SPECIES_NAMES_CSV)
            return NameIndex.from_csv(species, names)

        return await self._cache.get_or_fetch("name-index", TTL_POKEAPI, build)

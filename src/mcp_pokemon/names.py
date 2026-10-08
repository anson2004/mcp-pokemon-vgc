"""Multilingual name index (built from PokéAPI's source CSV, fetched online) and
Showdown <-> PokéAPI slug normalisation."""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .cache import TTL_POKEAPI, Http, TTLCache
from .models import Lang, LocalizedText, PokemonMatch

log = logging.getLogger(__name__)

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


# Localized form prefixes -> PokéAPI variety suffix. "超级暴飞龙" -> salamence + "mega".
FORM_PREFIXES: dict[str, str] = {
    "超级": "mega",
    "超級": "mega",
    "阿罗拉": "alola",
    "阿羅拉": "alola",
    "伽勒尔": "galar",
    "伽勒爾": "galar",
    "洗翠": "hisui",
    "帕底亚": "paldea",
    "帕底亞": "paldea",
    "灵兽": "therian",
    "靈獸": "therian",
    "化身": "incarnate",
    "メガ": "mega",
    "アローラ": "alola",
    "ガラル": "galar",
    "ヒスイ": "hisui",
    "パルデア": "paldea",
    "霊獣": "therian",
    "けしん": "incarnate",
    "mega ": "mega",
    "alolan ": "alola",
    "galarian ": "galar",
    "hisuian ": "hisui",
    "paldean ": "paldea",
}

ALIASES_FILE = Path(__file__).with_name("aliases.toml")
ALIASES_ENV = "MCP_POKEMON_ALIASES"


def load_aliases(extra_path: str | None = None) -> dict[str, str]:
    """alias (raw) -> PokéAPI slug, from the bundled file plus an optional user file."""
    out: dict[str, str] = {}
    paths = [ALIASES_FILE]
    extra = extra_path if extra_path is not None else os.environ.get(ALIASES_ENV)
    if extra:
        paths.append(Path(extra))
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("alias file not found: %s", path)
            continue
        for section in data.values():
            if isinstance(section, dict):
                for alias, slug in section.items():
                    if isinstance(slug, str):
                        out[str(alias)] = slug
    return out


class AmbiguousName(Exception):
    def __init__(
        self, query: str, candidates: list[str], entries: list[SpeciesEntry] | None = None
    ) -> None:
        self.query = query
        self.candidates = candidates
        self.entries: list[SpeciesEntry] = entries or []
        super().__init__(
            f"'{query}' matches several Pokémon: {', '.join(candidates)}. Please be more specific."
        )


@dataclass
class Resolution:
    entry: SpeciesEntry
    variety: str | None = None  # explicit variety slug from an alias
    form: str | None = None  # form suffix from a localized prefix (e.g. 'mega')
    note: str | None = None  # how a fuzzy match was made


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
    aliases: dict[str, tuple[SpeciesEntry, str | None]] = field(default_factory=dict)

    @classmethod
    def from_csv(
        cls, species_csv: str, names_csv: str, aliases: dict[str, str] | None = None
    ) -> NameIndex:
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
        index = cls(by_id=by_id, by_slug={e.slug: e for e in by_id.values()}, exact=exact)
        for alias, slug in (aliases or {}).items():
            resolved = index._species_for_slug(slug)
            if resolved is None:
                log.warning("alias %r -> unknown slug %r; skipped", alias, slug)
                continue
            entry, variety = resolved
            index.aliases[fold(alias)] = (entry, variety)
        return index

    def _species_for_slug(self, slug: str) -> tuple[SpeciesEntry, str | None] | None:
        """'garchomp' -> (garchomp, None); 'landorus-therian' -> (landorus, 'landorus-therian')."""
        if slug in self.by_slug:
            return self.by_slug[slug], None
        parts = slug.split("-")
        for i in range(len(parts) - 1, 0, -1):
            head = "-".join(parts[:i])
            if head in self.by_slug:
                return self.by_slug[head], slug
        return None

    def resolve(self, query: str) -> Resolution | None:
        """Exact name in any language, alias, localized form prefix, then unique substring.

        Raises AmbiguousName when a substring matches several species.
        """
        entry = self.lookup(query)
        if entry is not None:
            return Resolution(entry)
        q = fold(query)
        if q in self.aliases:
            entry, variety = self.aliases[q]
            return Resolution(entry, variety=variety, note=f"alias '{query}'")
        lowered = query.strip().lower()
        for prefix, form in FORM_PREFIXES.items():
            if lowered.startswith(prefix) and len(lowered) > len(prefix):
                rest = query.strip()[len(prefix) :]
                entry = self.lookup(rest)
                if entry is not None:
                    return Resolution(entry, form=form)
        return None

    def resolve_fuzzy(self, query: str) -> Resolution | None:
        """Unique substring match across all languages (e.g. 咆哮虎 -> 炽焰咆哮虎)."""
        hits = self.search(query, limit=200)  # all hits: callers may filter them further
        if len(hits) == 1:
            entry = hits[0]
            return Resolution(entry, note=f"'{query}' matched {self.display(entry, query)}")
        if len(hits) > 1:
            shown = [self.display(e, query) for e in hits[:8]]
            raise AmbiguousName(query, shown, hits)
        return None

    def suggestions(self, query: str, limit: int = 5) -> list[str]:
        return [self.display(e, query) for e in self.search(query, limit=limit)]

    @staticmethod
    def script_of(text: str) -> Lang:
        for ch in text:
            o = ord(ch)
            if 0x3040 <= o <= 0x30FF:
                return "ja"
            if 0xAC00 <= o <= 0xD7AF:
                return "ko"
            if 0x4E00 <= o <= 0x9FFF:
                return "zh-hans"
        return "en"

    def display(self, entry: SpeciesEntry, query: str = "") -> str:
        """Name in the query's script, with the English name for disambiguation."""
        lang = self.script_of(query)
        en = entry.names.get("en", entry.slug)
        if lang == "zh-hans":
            q = fold(query)
            hant = entry.names.get("zh-hant", "")
            hans = entry.names.get("zh-hans", "")
            local = hant if q and q in fold(hant) and q not in fold(hans) else hans or hant
        else:
            local = entry.names.get(lang, "")
        return f"{local} ({en})" if local and local != en else en

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

    def match(
        self, entry: SpeciesEntry, langs: tuple[Lang, ...], variety: str | None = None
    ) -> PokemonMatch:
        return PokemonMatch(
            species_id=entry.species_id,
            slug=entry.slug,
            names={lang: entry.names[lang] for lang in langs if lang in entry.names},
            variety=variety,
        )


class NameIndexLoader:
    def __init__(self, http: Http, cache: TTLCache) -> None:
        self._http = http
        self._cache = cache

    async def get(self) -> NameIndex:
        async def build() -> NameIndex:
            species = await self._http.get_text(SPECIES_CSV)
            names = await self._http.get_text(SPECIES_NAMES_CSV)
            return NameIndex.from_csv(species, names, load_aliases())

        return await self._cache.get_or_fetch("name-index", TTL_POKEAPI, build)

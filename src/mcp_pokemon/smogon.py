"""Smogon usage-stats client (chaos JSON) for VGC formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .cache import TTL_LISTING, TTL_SMOGON_FILE, Http, TTLCache
from .models import (
    CheckCounter,
    HeadToHead,
    RankedItem,
    UsageRank,
    VgcComparison,
    VgcFormat,
    VgcUsage,
)
from .names import AmbiguousName, NameIndex, Resolution, fold

STATS_BASE = "https://www.smogon.com/stats/"
MONTH_RE = re.compile(r'href="(\d{4}-\d{2})/"')
FILE_RE = re.compile(r'href="((gen\d+[a-z0-9]*vgc[a-z0-9]*)-(\d+)\.json)"')


class FormatNotFound(Exception):
    pass


class UsageNotFound(Exception):
    def __init__(self, query: str, suggestions: list[str]) -> None:
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"'{query}' is not in this usage file.{hint}")
        self.suggestions = suggestions


@dataclass
class ChaosFile:
    month: str
    format_id: str
    rating: int
    battles: int
    data: dict[str, dict[str, Any]]
    ranking: list[str]  # names sorted by usage desc
    folded: dict[str, str]  # fold(name) -> name

    @classmethod
    def from_json(cls, month: str, format_id: str, rating: int, raw: dict[str, Any]) -> ChaosFile:
        data: dict[str, dict[str, Any]] = raw["data"]
        ranking = sorted(data, key=lambda k: -data[k]["usage"])
        return cls(
            month=month,
            format_id=format_id,
            rating=rating,
            battles=int(raw["info"].get("number of battles", 0)),
            data=data,
            ranking=ranking,
            folded={fold(k): k for k in data},
        )


def parse_months(html: str) -> list[str]:
    return sorted(set(MONTH_RE.findall(html)))


def parse_formats(html: str, month: str) -> list[VgcFormat]:
    ratings: dict[str, set[int]] = {}
    for _file, fmt, rating in FILE_RE.findall(html):
        ratings.setdefault(fmt, set()).add(int(rating))
    return [
        VgcFormat(month=month, format_id=f, ratings=sorted(r), best_of_3=f.endswith("bo3"))
        for f, r in sorted(ratings.items())
    ]


def _check(name: str, v: Any) -> CheckCounter | None:
    """Parse a 'Checks and Counters' value: {'n','p','d'} (current) or [n, p, d] (older files)."""
    if isinstance(v, dict):
        n, p, d = v.get("n"), v.get("p"), v.get("d")
    elif isinstance(v, list | tuple) and len(v) >= 3:
        n, p, d = v[0], v[1], v[2]
    else:
        return None
    if n is None or p is None or d is None:
        return None
    return CheckCounter(
        name=name,
        encounters=round(float(n), 2),
        success_rate=round(float(p), 4),
        score=round(float(p) - 4 * float(d), 4),
    )


def _ranked(weights: dict[str, float], denom: float, top_n: int) -> list[RankedItem]:
    if denom <= 0:
        return []
    items = sorted(weights.items(), key=lambda kv: -kv[1])[:top_n]
    return [RankedItem(name=k, percent=round(100 * v / denom, 2)) for k, v in items if v > 0]


class SmogonClient:
    def __init__(self, http: Http, cache: TTLCache) -> None:
        self._http = http
        self._cache = cache

    async def months(self) -> list[str]:
        async def fetch() -> list[str]:
            return parse_months(await self._http.get_text(STATS_BASE))

        return await self._cache.get_or_fetch("smogon:months", TTL_LISTING, fetch)

    async def formats(self, month: str | None = None) -> list[VgcFormat]:
        if month is None:
            # Newest month that actually contains VGC files.
            for m in reversed(await self.months()):
                fmts = await self.formats(m)
                if fmts:
                    return fmts
            return []

        async def fetch() -> list[VgcFormat]:
            resp = await self._http.get(f"{STATS_BASE}{month}/chaos/")
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return parse_formats(resp.text, month)

        return await self._cache.get_or_fetch(f"smogon:formats:{month}", TTL_LISTING, fetch)

    async def default_format(self, month: str | None = None) -> VgcFormat:
        """Latest-regulation best-of-1 format (Champions preferred over SV)."""
        fmts = await self.formats(month)
        if not fmts:
            raise FormatNotFound(f"No VGC usage files for month {month or 'latest'}.")
        bo1 = [f for f in fmts if not f.best_of_3] or fmts
        champions = [f for f in bo1 if "champions" in f.format_id] or bo1
        return sorted(champions, key=lambda f: f.format_id)[-1]

    async def resolve_format(
        self, format_id: str | None, rating: int | None, month: str | None
    ) -> tuple[VgcFormat, int]:
        if format_id is None:
            fmt = await self.default_format(month)
        else:
            fmts = await self.formats(month)
            if month is None and not any(f.format_id == format_id for f in fmts):
                # Fall back to previous months that still have this format.
                for m in reversed(await self.months()):
                    fmts = await self.formats(m)
                    if any(f.format_id == format_id for f in fmts):
                        break
            match = [f for f in fmts if f.format_id == format_id]
            if not match:
                available = ", ".join(f.format_id for f in await self.formats(month))
                raise FormatNotFound(f"Format '{format_id}' not found. Available: {available}")
            fmt = match[0]
        if rating is None:
            rating = max(fmt.ratings)
        if rating not in fmt.ratings:
            raise FormatNotFound(
                f"Rating {rating} not available for {fmt.format_id}; choose from {fmt.ratings}"
            )
        return fmt, rating

    async def chaos(self, fmt: VgcFormat, rating: int) -> ChaosFile:
        url = f"{STATS_BASE}{fmt.month}/chaos/{fmt.format_id}-{rating}.json"

        async def fetch() -> ChaosFile:
            raw = await self._http.get_json(url)
            return ChaosFile.from_json(fmt.month, fmt.format_id, rating, raw)

        return await self._cache.get_or_fetch("smogon:chaos:" + url, TTL_SMOGON_FILE, fetch)

    # ---- queries -----------------------------------------------------------------

    @staticmethod
    def resolve_key(chaos: ChaosFile, query: str, index: NameIndex | None = None) -> str:
        """Map a user query (any language, nickname, Showdown or PokéAPI style) to a usage key."""
        q = fold(query)
        if q in chaos.folded:
            return chaos.folded[q]

        def by_prefix(folded_name: str) -> list[str]:
            return [name for f, name in chaos.folded.items() if f.startswith(folded_name)]

        def longest_key_prefix_of(folded_variety: str) -> str | None:
            keys = [name for f, name in chaos.folded.items() if folded_variety.startswith(f)]
            return max(keys, key=len) if keys else None

        candidates = by_prefix(q)
        if index is not None:
            res = index.resolve(query)
            if res is None and not candidates:
                try:
                    res = index.resolve_fuzzy(query)
                except AmbiguousName as exc:
                    # Keep only candidates that actually appear in this usage file.
                    in_file = [e for e in exc.entries if by_prefix(fold(e.names.get("en", e.slug)))]
                    if len(in_file) == 1:
                        res = Resolution(in_file[0])
                    else:
                        shown = in_file or exc.entries
                        raise UsageNotFound(
                            query, [index.display(e, query) for e in shown][:8]
                        ) from exc
            if res is not None:
                if res.variety:
                    key = longest_key_prefix_of(fold(res.variety))
                    if key:
                        return key
                en = fold(res.entry.names.get("en", res.entry.slug))
                if res.form:
                    key = longest_key_prefix_of(en + fold(res.form))
                    if key and key != chaos.folded.get(en):
                        return key
                elif en in chaos.folded:
                    return chaos.folded[en]
                candidates = by_prefix(en) or candidates
        if len(candidates) == 1:
            return candidates[0]
        raise UsageNotFound(query, sorted(candidates)[:8])

    @staticmethod
    def usage(chaos: ChaosFile, key: str, top_n: int = 5) -> VgcUsage:
        e = chaos.data[key]
        weight_total = sum(e.get("Abilities", {}).values()) or float(e.get("Raw count", 0))
        cc = [
            c
            for c in (_check(name, v) for name, v in e.get("Checks and Counters", {}).items())
            if c is not None
        ]
        cc.sort(key=lambda c: -c.score)
        tera = {k: v for k, v in e.get("Tera Types", {}).items() if k != "nothing"}
        return VgcUsage(
            pokemon=key,
            format_id=chaos.format_id,
            rating=chaos.rating,
            month=chaos.month,
            battles=chaos.battles,
            rank=chaos.ranking.index(key) + 1,
            usage_percent=round(100 * e["usage"], 2),
            raw_count=int(e.get("Raw count", 0)),
            moves=_ranked(e.get("Moves", {}), weight_total, top_n),
            items=_ranked(e.get("Items", {}), sum(e.get("Items", {}).values()), top_n),
            abilities=_ranked(e.get("Abilities", {}), weight_total, top_n),
            tera_types=_ranked(tera, sum(tera.values()), top_n) if tera else None,
            spreads=_ranked(e.get("Spreads", {}), sum(e.get("Spreads", {}).values()), top_n),
            teammates=_ranked(e.get("Teammates", {}), weight_total, top_n),
            checks_and_counters=cc[:top_n],
        )

    @staticmethod
    def top(chaos: ChaosFile, limit: int) -> list[UsageRank]:
        return [
            UsageRank(
                rank=i + 1,
                pokemon=name,
                usage_percent=round(100 * chaos.data[name]["usage"], 2),
                raw_count=int(chaos.data[name].get("Raw count", 0)),
            )
            for i, name in enumerate(chaos.ranking[:limit])
        ]

    @classmethod
    def compare(cls, chaos: ChaosFile, key_a: str, key_b: str, top_n: int = 5) -> VgcComparison:
        a = cls.usage(chaos, key_a, top_n)
        b = cls.usage(chaos, key_b, top_n)
        ea, eb = chaos.data[key_a], chaos.data[key_b]
        wa = sum(ea.get("Abilities", {}).values()) or 1.0
        wb = sum(eb.get("Abilities", {}).values()) or 1.0

        def check(target: dict[str, Any], name: str) -> CheckCounter | None:
            return _check(name, target.get("Checks and Counters", {}).get(name))

        def mate_rate(team: dict[str, Any], name: str, w: float) -> float | None:
            v = team.get("Teammates", {}).get(name)
            return None if v is None else round(100 * v / w, 2)

        ta = {t.name for t in _ranked(ea.get("Teammates", {}), wa, 15)}
        tb = {t.name for t in _ranked(eb.get("Teammates", {}), wb, 15)}
        shared = [n for n in chaos.ranking if n in ta and n in tb][:8]

        return VgcComparison(
            format_id=chaos.format_id,
            rating=chaos.rating,
            month=chaos.month,
            a=a,
            b=b,
            shared_teammates=shared,
            head_to_head=HeadToHead(
                a_as_check_to_b=check(eb, key_a),
                b_as_check_to_a=check(ea, key_b),
                a_teammate_rate_of_b=mate_rate(eb, key_a, wb),
                b_teammate_rate_of_a=mate_rate(ea, key_b, wa),
            ),
        )

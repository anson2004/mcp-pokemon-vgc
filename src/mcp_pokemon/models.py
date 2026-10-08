"""Pydantic models returned by the MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

Lang = Literal["en", "ja", "ja-hrkt", "zh-hans", "zh-hant", "ko"]
ALL_LANGS: tuple[Lang, ...] = ("en", "ja", "ja-hrkt", "zh-hans", "zh-hant", "ko")
DEFAULT_LANGS: tuple[Lang, ...] = ("en", "ja", "zh-hans", "zh-hant")

LocalizedText = dict[Lang, str]


class StatBlock(BaseModel):
    hp: int
    attack: int
    defense: int
    special_attack: int
    special_defense: int
    speed: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return (
            self.hp
            + self.attack
            + self.defense
            + self.special_attack
            + self.special_defense
            + self.speed
        )


class TypeSummary(BaseModel):
    slug: str
    names: LocalizedText


class AbilitySummary(BaseModel):
    slug: str
    names: LocalizedText
    is_hidden: bool


class PokemonMatch(BaseModel):
    species_id: int
    slug: str
    names: LocalizedText


class PokemonSummary(BaseModel):
    id: int = Field(description="PokéAPI pokemon (variety) id")
    species_id: int
    slug: str = Field(description="PokéAPI pokemon slug, e.g. 'urshifu-rapid-strike'")
    species_slug: str
    names: LocalizedText
    genus: LocalizedText
    types: list[TypeSummary]
    abilities: list[AbilitySummary]
    stats: StatBlock
    height_m: float
    weight_kg: float
    forms: list[str] = Field(description="All variety slugs of this species")
    flavor_text: LocalizedText
    is_legendary: bool
    is_mythical: bool
    note: str | None = Field(default=None, description="Set when the name was matched fuzzily")


class VgcFormat(BaseModel):
    month: str = Field(description="YYYY-MM")
    format_id: str = Field(description="Smogon format id, e.g. gen9championsvgc2026regmc")
    ratings: list[int]
    best_of_3: bool


class RankedItem(BaseModel):
    name: str
    percent: float


class CheckCounter(BaseModel):
    name: str
    encounters: float = Field(description="Weighted number of encounters")
    success_rate: float = Field(
        description="Fraction of encounters where this Pokémon KOed or forced out the target"
    )
    score: float = Field(
        description="Smogon score: success_rate - 4 * std_dev; higher is a better check"
    )


class VgcUsage(BaseModel):
    pokemon: str = Field(description="Smogon/Showdown name as it appears in the usage file")
    format_id: str
    rating: int
    month: str
    battles: int
    rank: int | None
    usage_percent: float
    raw_count: int
    moves: list[RankedItem]
    items: list[RankedItem]
    abilities: list[RankedItem]
    tera_types: list[RankedItem]
    spreads: list[RankedItem]
    teammates: list[RankedItem]
    checks_and_counters: list[CheckCounter]
    note: str | None = None


class HeadToHead(BaseModel):
    a_as_check_to_b: CheckCounter | None = Field(
        description="How well A checks B, from B's counters list"
    )
    b_as_check_to_a: CheckCounter | None
    a_teammate_rate_of_b: float | None = Field(description="Percent of B's teams that also run A")
    b_teammate_rate_of_a: float | None


class VgcComparison(BaseModel):
    format_id: str
    rating: int
    month: str
    a: VgcUsage
    b: VgcUsage
    shared_teammates: list[str]
    head_to_head: HeadToHead


class UsageRank(BaseModel):
    rank: int
    pokemon: str
    usage_percent: float
    raw_count: int

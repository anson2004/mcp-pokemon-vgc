"""Pydantic models returned by the MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

Lang = Literal["en", "ja", "ja-hrkt", "zh-hans", "zh-hant", "ko"]
ALL_LANGS: tuple[Lang, ...] = ("en", "ja", "ja-hrkt", "zh-hans", "zh-hant", "ko")
DEFAULT_LANGS: tuple[Lang, ...] = ("en", "zh-hans", "ja")

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
    slug: str = Field(description="PokéAPI species slug")
    names: LocalizedText
    variety: str | None = Field(
        default=None,
        description="Variety slug when the query implied a form, e.g. 'landorus-therian'",
    )


class NameResolution(BaseModel):
    query: str
    resolved: PokemonMatch | None = Field(
        description="The single match, or null if ambiguous/unknown"
    )
    note: str | None = Field(default=None, description="How a fuzzy or alias match was made")
    candidates: list[PokemonMatch] = Field(
        default_factory=list, description="Possible matches when the query is not unique"
    )


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
    flavor_text: LocalizedText | None = Field(
        default=None, description="Pokédex entry per language; only when include_flavor_text=true"
    )
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
    tera_types: list[RankedItem] | None = Field(
        default=None, description="Null in formats without Terastallization (Champions)"
    )
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


# ---- team pastes and battle prediction (phase 3) -----------------------------------------

StatKey = Literal["hp", "atk", "def", "spa", "spd", "spe"]
SpreadScale = Literal["ev", "points"]


class Spread(BaseModel):
    scale: SpreadScale = Field(
        description="'ev' = Scarlet/Violet 0–252 EVs; 'points' = Champions 0–32 stat points"
    )
    values: dict[StatKey, int]

    @model_validator(mode="after")
    def _check_range(self) -> Spread:
        cap = 252 if self.scale == "ev" else 32
        for stat, v in self.values.items():
            if v < 0 or v > cap:
                raise ValueError(f"{stat}={v} outside 0–{cap} for scale '{self.scale}'")
        if self.scale == "ev" and sum(self.values.values()) > 510:
            raise ValueError("EV total exceeds 510")
        return self


class TeamMember(BaseModel):
    species: str = Field(description="Species as written in the paste")
    key: str | None = Field(default=None, description="Smogon usage-file key once resolved")
    nickname: str | None = None
    item: str | None = None
    ability: str | None = None
    tera: str | None = None
    nature: str | None = None
    level: int = Field(default=50, ge=1, le=100)
    moves: list[str] = Field(default_factory=list, max_length=4)
    spread: Spread | None = None


class Team(BaseModel):
    members: list[TeamMember] = Field(min_length=1, max_length=6)
    brought: list[str] | None = Field(
        default=None, max_length=4, description="The four picked for a game, if known"
    )
    warnings: list[str] = Field(default_factory=list)


class MatchupEdge(BaseModel):
    a: str
    b: str
    edge: float = Field(description="score(a checks b) − score(b checks a); positive favours a")
    a_checks_b: float | None = None
    b_checks_a: float | None = None
    no_data: bool = False


class SpeedSummary(BaseModel):
    a_faster: int
    b_faster: int
    ties: int
    a_speed_control: list[str] = Field(default_factory=list)
    b_speed_control: list[str] = Field(default_factory=list)
    note: str | None = None

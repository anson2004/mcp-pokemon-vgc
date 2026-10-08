"""MCP server entry point: registers tools and resources."""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .deps import get_deps
from .models import (
    Lang,
    NameResolution,
    PokemonSummary,
    UsageRank,
    VgcComparison,
    VgcFormat,
    VgcUsage,
)
from .names import AmbiguousName
from .pokeapi import PokemonNotFound
from .smogon import FormatNotFound, UsageNotFound
from .tools import describe as describe_tools
from .tools import vgc as vgc_tools

log = logging.getLogger(__name__)

mcp = MCPServer(
    "mcp-pokemon",
    instructions=(
        "Pokémon data in English, Japanese and Chinese (PokéAPI) plus VGC usage statistics "
        "(Smogon). Use describe_pokemon for Pokédex facts, resolve_pokemon only to turn a "
        "nickname into official names, and "
        "list_vgc_formats / top_vgc_usage / get_vgc_usage / compare_vgc for competitive data. "
        "Pokémon names may be given in any supported language, as common nicknames or "
        "short forms (咆哮虎, ガブ, Lando-T), with localized form prefixes (超级暴飞龙, "
        "メガボーマンダ, 灵兽土地云), or in Showdown style (e.g. 'Urshifu-Rapid-Strike')."
    ),
)

LangsParam = Annotated[
    list[Lang] | None,
    Field(description="Languages to include; default en, zh-hans, ja. Also zh-hant, ja-hrkt, ko."),
]
FormatParam = Annotated[
    str | None,
    Field(description="Smogon format id (see list_vgc_formats); default latest Champions bo1"),
]
RatingParam = Annotated[
    int | None, Field(description="Rating cutoff; default the highest available")
]
MonthParam = Annotated[str | None, Field(description="YYYY-MM; default latest month")]


def _wrap(exc: Exception) -> ToolError:
    if isinstance(exc, PokemonNotFound | UsageNotFound | FormatNotFound | AmbiguousName):
        return ToolError(str(exc))
    log.exception("tool failed")
    return ToolError(f"{type(exc).__name__}: {exc}")


@mcp.tool()
async def resolve_pokemon(
    query: Annotated[
        str,
        Field(
            description="Nickname, short form or name in any language, e.g. 咆哮虎, ガブ, Lando-T"
        ),
    ],
    langs: LangsParam = None,
    limit: Annotated[int, Field(description="Max candidates when ambiguous")] = 8,
) -> NameResolution:
    """Turn a nickname or partial name into official names (en/ja/zh). Cheap: no stats fetched.

    Use only when the user wants the name itself; describe_pokemon and the VGC tools already
    accept nicknames directly.
    """
    try:
        return await describe_tools.resolve_pokemon(get_deps(), query, langs, limit)
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.tool()
async def describe_pokemon(
    name: Annotated[
        str,
        Field(
            description="Pokémon name in any language, or a form slug like 'urshifu-rapid-strike'"
        ),
    ],
    langs: LangsParam = None,
    form: Annotated[
        str | None, Field(description="Optional form, e.g. 'alola', 'mega', 'therian'")
    ] = None,
    include_flavor_text: Annotated[
        bool, Field(description="Also return the Pokédex entry text per language (longer output)")
    ] = False,
) -> PokemonSummary:
    """Describe a Pokémon: localized names, genus, types, abilities, base stats, size."""
    try:
        return await describe_tools.describe_pokemon(
            get_deps(), name, langs, form, include_flavor_text
        )
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.tool()
async def list_vgc_formats(month: MonthParam = None) -> list[VgcFormat]:
    """List VGC formats and rating cutoffs available in Smogon usage stats for a month."""
    try:
        return await vgc_tools.list_vgc_formats(get_deps(), month)
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.tool()
async def top_vgc_usage(
    format_id: FormatParam = None,
    rating: RatingParam = None,
    month: MonthParam = None,
    limit: int = 30,
) -> list[UsageRank]:
    """Usage ranking for a VGC format (most-used Pokémon first)."""
    try:
        return await vgc_tools.top_vgc_usage(get_deps(), format_id, rating, month, limit)
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.tool()
async def get_vgc_usage(
    pokemon: Annotated[str, Field(description="Pokémon name in any language or Showdown style")],
    format_id: FormatParam = None,
    rating: RatingParam = None,
    month: MonthParam = None,
    top_n: int = 5,
) -> VgcUsage:
    """VGC usage profile: usage %, moves, items, abilities, tera, spreads, teammates, checks."""
    try:
        return await vgc_tools.get_vgc_usage(get_deps(), pokemon, format_id, rating, month, top_n)
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.tool()
async def compare_vgc(
    pokemon_a: str,
    pokemon_b: str,
    format_id: FormatParam = None,
    rating: RatingParam = None,
    month: MonthParam = None,
    top_n: int = 5,
) -> VgcComparison:
    """Compare two Pokémon's VGC usage side by side, with shared teammates and head-to-head data."""
    try:
        return await vgc_tools.compare_vgc(
            get_deps(), pokemon_a, pokemon_b, format_id, rating, month, top_n
        )
    except Exception as exc:
        raise _wrap(exc) from exc


@mcp.resource("pokemon://formats/latest", mime_type="application/json")
async def latest_formats() -> str:
    """VGC formats available for the latest Smogon stats month."""
    fmts = await vgc_tools.list_vgc_formats(get_deps())
    return "[" + ",".join(f.model_dump_json() for f in fmts) + "]"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()

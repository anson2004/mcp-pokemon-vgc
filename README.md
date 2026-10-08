# mcp-pokemon

A typed [MCP](https://modelcontextprotocol.io) server (Python, `mcp` SDK 2.x, Pydantic) that

1. describes Pokémon in **English, Japanese and Chinese** (Simplified + Traditional, Korean optional), and
2. compares Pokémon on **VGC usage statistics** (usage %, moves, items, abilities, spreads, teammates, checks).

All data is fetched live at runtime and cached in memory only. Nothing is vendored.

| Data | Source | Key |
|---|---|---|
| Base stats, types, abilities, localized names, flavor text | [PokéAPI](https://pokeapi.co) | none |
| Any-language name index | PokéAPI source CSV on GitHub (`pokemon_species_names.csv`) | none |
| VGC usage | [Smogon usage stats](https://www.smogon.com/stats/) chaos JSON | none |

## Install

```bash
uv sync            # Python >= 3.12
uv run mcp-pokemon # starts the stdio server
```

### Claude Code

```bash
claude mcp add pokemon -- uv --directory /absolute/path/to/mcp-pokemon run mcp-pokemon
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "pokemon": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mcp-pokemon", "run", "mcp-pokemon"]
    }
  }
}
```

### Inspect

```bash
uv run mcp dev src/mcp_pokemon/server.py
```

## Tools

| Tool | What it does |
|---|---|
| `resolve_pokemon(query, langs?, limit?)` | Nickname or partial name in any language → official names (`咆哮虎` → 炽焰咆哮虎 / Incineroar). Returns one match, or a candidates list when ambiguous. Index only, no stats. |
| `describe_pokemon(name, langs?, form?, include_flavor_text?)` | Localized names, genus, types, abilities, base stats, size. Pokédex entry text only with `include_flavor_text=true`. Accepts `皮卡丘`, `ピカチュウ`, `Pikachu`, Showdown names like `Urshifu-Rapid-Strike`, or `form="alola"`. |
| `list_vgc_formats(month?)` | VGC formats and rating cutoffs available on Smogon for a month (default: latest). |
| `top_vgc_usage(format_id?, rating?, month?, limit?)` | Usage ranking. |
| `get_vgc_usage(pokemon, format_id?, rating?, month?, top_n?)` | One Pokémon's usage profile. `top_n` (default 5) caps every list. |
| `compare_vgc(pokemon_a, pokemon_b, ...)` | Two profiles side by side, shared teammates (max 8), head-to-head check scores and teammate rates. |

Resource: `pokemon://formats/latest`.

Defaults: languages `en`, `zh-hans`, `ja` (add `zh-hant`, `ja-hrkt`, `ko` via `langs`); latest month,
newest Pokémon Champions best-of-1 format, highest rating cutoff.

## Name resolution

Every tool that takes a Pokémon name resolves it in this order (`resolve_pokemon` exposes the same chain directly):

1. **Exact** official name in any supported language, or a PokéAPI / Showdown slug
   (`Pikachu`, `ピカチュウ`, `皮卡丘`, `Urshifu-Rapid-Strike`).
2. **Alias** from [`aliases.toml`](src/mcp_pokemon/aliases.toml): community nicknames that are not
   part of an official name (`Lando-T`, `皮神`, `鬼龙`, `ガブ`).
3. **Localized form prefix**: `超级暴飞龙` / `メガボーマンダ` → Mega Salamence, `阿罗拉九尾` → Alolan
   Ninetales, `灵兽土地云` / `霊獣ランドロス` → Landorus-Therian.
4. **Unique substring** of any official name: `咆哮虎` → 炽焰咆哮虎 (Incineroar), `Chomp` → Garchomp.
   The result carries a `note` saying how it matched. If several Pokémon match, the tool returns
   an error listing them in the script you typed (e.g. `炽焰咆哮虎 (Incineroar)`).

Add your own nicknames in a TOML file with the same layout and point `MCP_POKEMON_ALIASES` at it.

## Notes

- Pokémon Champions formats report spreads on a 0–32 point scale and have no Tera types.
  Scarlet/Violet formats (`gen9vgc2024regg`) use 0–252 EVs. Spreads are reported verbatim.
- Percentages follow Smogon's moveset reports: moves, abilities and teammates are divided by
  the Pokémon's total weight; items, spreads and tera types by their category total.
- Check score = `p − 4·d` (Smogon's formula); higher means a more reliable check.
- Cache TTLs: PokéAPI 24 h, usage files 6 h, directory listings 1 h. A 1760-rating usage file
  is ~16 MB and is downloaded once per process per format.

## Development

```bash
uv run pytest
uv run ruff check src tests && uv run mypy src
```

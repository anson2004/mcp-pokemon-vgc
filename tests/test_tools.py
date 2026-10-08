import pytest

from mcp_pokemon.pokeapi import PokemonNotFound
from mcp_pokemon.tools import describe, vgc


async def test_describe_pikachu_multilingual(deps, mocked):
    s = await describe.describe_pokemon(deps, "皮卡丘")
    assert s.slug == "pikachu" and s.species_id == 25
    assert s.names == {"en": "Pikachu", "ja": "ピカチュウ", "zh-hans": "皮卡丘"}
    assert s.genus["ja"] == "ねずみポケモン"
    assert [t.slug for t in s.types] == ["electric"]
    assert s.types[0].names["zh-hans"] == "电"
    assert s.stats.total == 320 and s.stats.speed == 90
    assert s.abilities[0].slug == "static" and s.abilities[1].is_hidden
    assert s.height_m == 0.4 and s.weight_kg == 6.0
    assert s.flavor_text is None
    s = await describe.describe_pokemon(
        deps, "皮卡丘", langs=["en", "zh-hant"], include_flavor_text=True
    )
    assert s.names == {"en": "Pikachu", "zh-hant": "皮卡丘"}
    assert set(s.flavor_text) == {"en", "zh-hant"}


async def test_describe_form_resolution(deps, mocked):
    s = await describe.describe_pokemon(deps, "Urshifu-Rapid-Strike", langs=["en", "ja"])
    assert s.slug == "urshifu-rapid-strike"
    s = await describe.describe_pokemon(deps, "Urshifu")
    assert s.slug == "urshifu-single-strike"
    s = await describe.describe_pokemon(deps, "ウーラオス", form="rapid-strike")
    assert s.slug == "urshifu-rapid-strike"
    assert s.forms[:2] == ["urshifu-single-strike", "urshifu-rapid-strike"]


async def test_describe_unknown_suggests(deps, mocked):
    with pytest.raises(PokemonNotFound) as ei:
        await describe.describe_pokemon(deps, "pikachuuuu")
    assert "No Pokémon matches" in str(ei.value)


async def test_resolve_pokemon(deps, mocked):
    r = await describe.resolve_pokemon(deps, "ピカ", langs=["en"])
    assert r.resolved is not None and r.resolved.names == {"en": "Pikachu"}
    assert r.resolved.variety is None and "matched" in r.note

    r = await describe.resolve_pokemon(deps, "皮卡丘", langs=["en", "zh-hans"])
    assert r.resolved.slug == "pikachu" and r.note is None and r.candidates == []

    r = await describe.resolve_pokemon(deps, "urshifu-rs", langs=["en"])
    assert r.resolved.slug == "urshifu" and r.resolved.variety == "urshifu-rapid-strike"

    r = await describe.resolve_pokemon(deps, "超级暴飞龙", langs=["en"])
    assert r.resolved.slug == "salamence" and r.resolved.variety == "salamence-mega"

    r = await describe.resolve_pokemon(deps, "チュ", langs=["en"])
    assert r.resolved is None and len(r.candidates) == 3 and "several" in r.note
    assert {c.slug for c in r.candidates} == {"pikachu", "raichu", "pichu"}

    r = await describe.resolve_pokemon(deps, "zzzz")
    assert r.resolved is None and r.candidates == [] and r.note is None


async def test_vgc_tools(deps, mocked):
    fmts = await vgc.list_vgc_formats(deps)
    assert fmts[0].month == "2026-09"
    top = await vgc.top_vgc_usage(deps, limit=2)
    assert [t.pokemon for t in top] == ["Rillaboom", "Sneasler"]
    u = await vgc.get_vgc_usage(deps, "ピカチュウ")
    assert (
        u.pokemon == "Pikachu" and u.format_id == "gen9championsvgc2026regmc" and u.rating == 1760
    )
    u = await vgc.get_vgc_usage(deps, "Rillaboom", rating=1500)
    assert u.rating == 1500
    cmp = await vgc.compare_vgc(deps, "Rillaboom", "Incineroar")
    assert cmp.month == "2026-09"
    # only the two listed chaos files were downloaded, and each once
    calls = [c.request.url.path for c in mocked.calls if c.request.url.path.endswith(".json")]
    assert sorted(set(calls)) == sorted(calls)

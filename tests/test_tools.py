import pytest

from mcp_pokemon.pokeapi import PokemonNotFound
from mcp_pokemon.smogon import UsageNotFound
from mcp_pokemon.teams import TeamParseError
from mcp_pokemon.tools import describe, predict, vgc

from .conftest import fixture


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


async def test_parse_vgc_team_tool(deps, mocked):
    team = await predict.parse_vgc_team(deps, fixture("team_champions.txt"))
    assert [m.key for m in team.members] == [
        "Rillaboom",
        "Incineroar",
        "Salamence-Mega",
        "Sneasler",
        "Pikachu",
        "Urshifu-Single-Strike",
    ]
    assert team.members[0].spread is not None and team.members[0].spread.scale == "points"
    assert any("no checks/counters" in w for w in team.warnings)

    with pytest.raises(UsageNotFound):
        await predict.parse_vgc_team(deps, "Garchomp @ Choice Scarf\nAbility: Rough Skin")
    with pytest.raises(TeamParseError):
        await predict.parse_vgc_team(deps, "   \n\n")


async def test_predict_vgc_battle_tool(deps, mocked):
    p = await predict.predict_vgc_battle(
        deps, fixture("team_champions.txt"), fixture("team_sv.txt"), rating=1500
    )
    assert p.format_id == "gen9championsvgc2026regmc" and p.rating == 1500
    assert 0 < p.win_probability_a < 1
    assert len(p.bring_a.pokemon) == 4 and p.bring_a.source == "minimax"
    assert len(p.bring_b.pokemon) == 4
    assert p.speed.note is None  # every member has a PokéAPI stats stub
    assert p.speed.a_faster + p.speed.b_faster + p.speed.ties == 16
    # pairs without check data fall back to the type chart fetched from PokéAPI
    assert any(e.basis == "types" for e in p.key_matchups) or any(
        "type chart" in w for w in p.warnings
    )
    assert any("Tera" in w for w in p.warnings)

    q = await predict.predict_vgc_battle(
        deps,
        fixture("team_champions.txt"),
        fixture("team_sv.txt"),
        rating=1500,
        brought_a=["rilla", "Mence", "Sneasler", "Pikachu"],
        brought_b=["Cat", "Rillaboom", "Sneasler", "Urshifu"],
    )
    assert q.bring_a.pokemon == ["Rillaboom", "Salamence-Mega", "Sneasler", "Pikachu"]
    assert q.bring_a.source == "given" and q.bring_b.source == "given"
    assert q.bring_b.pokemon[0] == "Incineroar"  # nickname "Cat" from the paste header

    r = await predict.predict_vgc_battle(
        deps, fixture("team_sv.txt"), fixture("team_champions.txt"), rating=1500
    )
    assert r.win_probability_a == pytest.approx(1 - p.win_probability_a, abs=1e-3)
    # one chaos download, pokemon/type lookups cached across the three calls
    paths = [c.request.url.path for c in mocked.calls if c.request.url.path.endswith(".json")]
    assert len(paths) == 1


async def test_analyze_vgc_team_tool(deps, mocked):
    rep = await predict.analyze_vgc_team(deps, fixture("team_sv.txt"), top_n=3)
    assert rep.pokemon == ["Incineroar", "Rillaboom", "Sneasler", "Urshifu-Rapid-Strike"]
    assert rep.speed_control == []
    assert 1 <= len(rep.threats) <= 3
    assert all(t.pokemon not in rep.pokemon for t in rep.threats)
    assert all(set(t.beats) <= set(rep.pokemon) for t in rep.threats)
    edges = [t.edge for t in rep.threats]
    assert edges == sorted(edges, reverse=True)

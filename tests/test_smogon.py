import json

import pytest

from mcp_pokemon.names import NameIndex
from mcp_pokemon.smogon import ChaosFile, SmogonClient, UsageNotFound, parse_formats, parse_months

from .conftest import fixture


def chaos() -> ChaosFile:
    return ChaosFile.from_json(
        "2026-09", "gen9championsvgc2026regmc", 1760, json.loads(fixture("chaos_small.json"))
    )


def test_parse_listings():
    assert parse_months(fixture("smogon_index.html"))[-1] == "2026-09"
    fmts = parse_formats(fixture("smogon_chaos_index.html"), "2026-09")
    ids = [f.format_id for f in fmts]
    assert "gen9championsvgc2026regmc" in ids and "gen9championsvgc2026regmcbo3" in ids
    assert "gen9ou" not in ids
    f = next(f for f in fmts if f.format_id == "gen9championsvgc2026regmc")
    assert f.ratings == [0, 1500, 1630, 1760] and not f.best_of_3


def test_ranking_and_usage():
    c = chaos()
    assert c.ranking[:3] == ["Rillaboom", "Sneasler", "Incineroar"]
    u = SmogonClient.usage(c, "Rillaboom", top_n=3)
    assert u.rank == 1 and u.usage_percent == 46.6 and u.raw_count == 6000
    assert u.moves[0].name == "grassyglide" and u.moves[0].percent == pytest.approx(98.65, abs=0.1)
    assert u.abilities[0].percent == pytest.approx(99.8, abs=0.1)
    assert u.items[0].percent == pytest.approx(77.6, abs=0.1)
    assert u.tera_types is None  # Champions: no Tera
    assert u.teammates[0].name == "Incineroar" and u.teammates[0].percent == pytest.approx(
        41.0, abs=0.1
    )
    assert u.checks_and_counters[0].name == "Sneasler"
    assert u.checks_and_counters[0].score == pytest.approx(0.45 - 4 * 0.085, abs=1e-3)


def test_resolve_key_variants():
    c = chaos()
    idx = NameIndex.from_csv(fixture("pokemon_species.csv"), fixture("pokemon_species_names.csv"))
    assert SmogonClient.resolve_key(c, "rillaboom") == "Rillaboom"
    assert SmogonClient.resolve_key(c, "urshifu rapid strike") == "Urshifu-Rapid-Strike"
    assert SmogonClient.resolve_key(c, "皮卡丘", idx) == "Pikachu"
    assert SmogonClient.resolve_key(c, "Salam") == "Salamence-Mega"
    with pytest.raises(UsageNotFound) as ei:
        SmogonClient.resolve_key(c, "Urshifu", idx)
    assert ei.value.suggestions == ["Urshifu-Rapid-Strike", "Urshifu-Single-Strike"]


def test_compare():
    c = chaos()
    cmp = SmogonClient.compare(c, "Rillaboom", "Incineroar")
    assert cmp.a.pokemon == "Rillaboom" and cmp.b.pokemon == "Incineroar"
    assert "Sneasler" in cmp.shared_teammates
    assert cmp.head_to_head.b_teammate_rate_of_a == pytest.approx(41.0, abs=0.1)
    assert cmp.head_to_head.a_as_check_to_b is not None
    assert cmp.head_to_head.a_as_check_to_b.name == "Rillaboom"

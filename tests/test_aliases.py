import pytest

from mcp_pokemon.names import AmbiguousName, NameIndex, load_aliases
from mcp_pokemon.pokeapi import PokemonNotFound
from mcp_pokemon.smogon import SmogonClient, UsageNotFound
from mcp_pokemon.tools import describe, vgc

from .conftest import fixture
from .test_smogon import chaos


def index() -> NameIndex:
    return NameIndex.from_csv(
        fixture("pokemon_species.csv"), fixture("pokemon_species_names.csv"), load_aliases()
    )


def test_bundled_alias_file_loads_and_targets_resolve(tmp_path):
    aliases = load_aliases()
    assert aliases["皮神"] == "pikachu" and aliases["lando-t"] == "landorus-therian"
    extra = tmp_path / "extra.toml"
    extra.write_text('[zh]\n"小电鼠" = "pikachu"\n', encoding="utf-8")
    assert load_aliases(str(extra))["小电鼠"] == "pikachu"


def test_resolve_exact_alias_prefix_and_fuzzy():
    idx = index()
    assert idx.resolve("皮卡丘").entry.species_id == 25
    r = idx.resolve("皮神")
    assert r.entry.species_id == 25 and r.note.startswith("alias")
    r = idx.resolve("urshifu-rs")
    assert r.entry.species_id == 892 and r.variety == "urshifu-rapid-strike"
    r = idx.resolve("超级暴飞龙")
    assert r.entry.species_id == 373 and r.form == "mega"
    r = idx.resolve("メガボーマンダ")
    assert r.entry.species_id == 373 and r.form == "mega"
    assert idx.resolve("咆哮虎") is None  # not exact: handled by the fuzzy layer
    r = idx.resolve_fuzzy("咆哮虎")
    assert r.entry.species_id == 727 and "炽焰咆哮虎 (Incineroar)" in r.note
    with pytest.raises(AmbiguousName) as ei:
        idx.resolve_fuzzy("チュ")  # ピカチュウ, ライチュウ, ピチュー
    assert len(ei.value.candidates) > 1 and "(" in ei.value.candidates[0]


def test_suggestions_use_query_script():
    idx = index()
    assert idx.suggestions("咆哮虎") == ["炽焰咆哮虎 (Incineroar)"]
    assert idx.suggestions("熾焰") == ["熾焰咆哮虎 (Incineroar)"]
    assert idx.suggestions("ガオ") == ["ガオガエン (Incineroar)"]
    assert idx.suggestions("incin") == ["Incineroar"]


def test_smogon_key_from_alias_prefix_and_fuzzy():
    c, idx = chaos(), index()
    assert SmogonClient.resolve_key(c, "咆哮虎", idx) == "Incineroar"
    assert SmogonClient.resolve_key(c, "urshifu-rs", idx) == "Urshifu-Rapid-Strike"
    assert SmogonClient.resolve_key(c, "超级暴飞龙", idx) == "Salamence-Mega"
    assert SmogonClient.resolve_key(c, "皮神", idx) == "Pikachu"
    # ambiguous in the Pokédex (ピカチュウ/ライチュウ/ピチュー) but only Pikachu is in the file
    assert SmogonClient.resolve_key(c, "チュ", idx) == "Pikachu"
    with pytest.raises(UsageNotFound) as ei:
        SmogonClient.resolve_key(c, "ウ", idx)  # matches nothing in the file uniquely
    assert len(ei.value.suggestions) <= 8


async def test_describe_fuzzy_and_alias(deps, mocked):
    s = await describe.describe_pokemon(deps, "カチュ", langs=["en"])
    assert s.slug == "pikachu" and "ピカチュウ (Pikachu)" in s.note
    s = await describe.describe_pokemon(deps, "皮神", langs=["en"])
    assert s.slug == "pikachu" and s.note == "alias '皮神'"
    s = await describe.describe_pokemon(deps, "urshifu-rs", langs=["en"])
    assert s.slug == "urshifu-rapid-strike"
    s = await describe.describe_pokemon(deps, "ピカチュウ", langs=["en"])
    assert s.note is None
    with pytest.raises(PokemonNotFound):
        await describe.describe_pokemon(deps, "zzzz")


async def test_vgc_usage_fuzzy(deps, mocked):
    u = await vgc.get_vgc_usage(deps, "咆哮虎")
    assert u.pokemon == "Incineroar"

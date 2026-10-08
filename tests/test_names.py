from mcp_pokemon.names import NameIndex, fold, to_pokeapi_slug

from .conftest import fixture


def test_fold_handles_scripts_and_punctuation():
    assert fold("Mr. Mime") == "mrmime"
    assert fold("Flabébé") == "flabebe"
    assert fold("Urshifu-Rapid-Strike") == "urshifurapidstrike"
    assert fold("皮卡丘") == "皮卡丘"
    assert fold("ピカチュウ") == "ピカチュウ"  # dakuten must survive
    assert fold("ガブリアス") != fold("カフリアス")


def test_to_pokeapi_slug():
    assert to_pokeapi_slug("Mr. Mime") == "mr-mime"
    assert to_pokeapi_slug("Farfetch'd") == "farfetchd"
    assert to_pokeapi_slug("Urshifu") == "urshifu-single-strike"
    assert to_pokeapi_slug("Ogerpon-Wellspring") == "ogerpon-wellspring-mask"
    assert to_pokeapi_slug("Flutter Mane") == "flutter-mane"
    assert to_pokeapi_slug("Landorus-Therian") == "landorus-therian"


def test_index_lookup_in_all_languages():
    idx = NameIndex.from_csv(fixture("pokemon_species.csv"), fixture("pokemon_species_names.csv"))
    for q in ("Pikachu", "pikachu", "ピカチュウ", "皮卡丘", "피카츄", "PIKACHU"):
        entry = idx.lookup(q)
        assert entry is not None and entry.species_id == 25, q
    assert idx.lookup("nonexistent") is None
    assert idx.by_id[25].names["zh-hans"] == "皮卡丘"
    assert idx.by_id[25].genus["en"] == "Mouse Pokémon"


def test_index_search_prefix_and_substring():
    idx = NameIndex.from_csv(fixture("pokemon_species.csv"), fixture("pokemon_species_names.csv"))
    ids = [e.species_id for e in idx.search("pika")]
    assert ids[0] == 25
    assert [e.species_id for e in idx.search("ウーラ")] == [892]

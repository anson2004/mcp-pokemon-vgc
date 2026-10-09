import pytest

from mcp_pokemon.teams import TeamParseError, parse_team, to_id

from .conftest import fixture


def test_sv_paste_full_lines():
    team = parse_team(fixture("team_sv.txt"), scale="ev")
    assert [m.species for m in team.members] == [
        "Incineroar",
        "Rillaboom",
        "Sneasler",
        "Urshifu-Rapid-Strike",
    ]
    cat = team.members[0]
    assert cat.nickname == "Cat" and cat.item == "Safety Goggles"
    assert cat.ability == "Intimidate" and cat.level == 50 and cat.tera == "Ghost"
    assert cat.nature == "Careful"
    assert cat.spread is not None and cat.spread.scale == "ev"
    assert cat.spread.values == {"hp": 252, "atk": 4, "spd": 252}
    assert cat.moves == ["Fake Out", "Knock Off", "Parting Shot", "Flare Blitz"]
    assert team.members[2].nickname is None and team.members[2].item == "Focus Sash"
    assert team.warnings == ["IVs ignored (no IVs in this model)"]


def test_champions_paste_points():
    team = parse_team(fixture("team_champions.txt"), scale="points")
    assert len(team.members) == 6
    r = team.members[0]
    assert r.spread is not None and r.spread.scale == "points"
    assert r.spread.values == {"hp": 32, "atk": 32, "spd": 2}
    assert r.tera is None and r.nature == "Adamant"
    assert team.warnings == []


def test_evs_label_with_points_hint():
    team = parse_team("Rillaboom\nEVs: 32 HP / 32 Atk\n", scale="points")
    sp = team.members[0].spread
    assert sp is not None and sp.scale == "points" and sp.values == {"hp": 32, "atk": 32}


def test_points_hint_but_ev_values():
    team = parse_team("Rillaboom\nStat Points: 252 HP / 4 Atk\n", scale="points")
    sp = team.members[0].spread
    assert sp is not None and sp.scale == "ev" and sp.values["hp"] == 252
    assert any("exceeds 32" in w for w in team.warnings)


def test_open_team_sheet_without_spread():
    team = parse_team("Incineroar @ Safety Goggles\nAbility: Intimidate\n- Fake Out\n- Knock Off")
    m = team.members[0]
    assert m.spread is None and m.nature is None and m.level == 50
    assert team.warnings == []


@pytest.mark.parametrize(
    ("header", "species", "nickname", "item"),
    [
        ("Nick (Incineroar) (M) @ Safety Goggles", "Incineroar", "Nick", "Safety Goggles"),
        ("Incineroar (F)", "Incineroar", None, None),
        ("Incineroar @ Safety Goggles", "Incineroar", None, None),
        ("Incineroar", "Incineroar", None, None),
        ("Big Cat (Incineroar) @ Sitrus Berry", "Incineroar", "Big Cat", "Sitrus Berry"),
        ("Urshifu-Rapid-Strike (M) @ Choice Scarf", "Urshifu-Rapid-Strike", None, "Choice Scarf"),
    ],
)
def test_header_variants(header, species, nickname, item):
    m = parse_team(header).members[0]
    assert m.species == species and m.nickname == nickname
    if item is not None or "@" in header:
        assert m.item == (item if item is not None else "Safety Goggles")


def test_unknown_line_is_warning():
    team = parse_team("Rillaboom\nFoo: bar\n- Fake Out")
    assert team.members[0].moves == ["Fake Out"]
    assert team.warnings == ["line 2 ignored: 'Foo: bar'"]


def test_structural_errors():
    with pytest.raises(TeamParseError, match="more than four moves"):
        parse_team("Rillaboom\n- a\n- b\n- c\n- d\n- e")
    seven = "\n\n".join(f"Mon{i}" for i in range(7))
    with pytest.raises(TeamParseError, match="more than six"):
        parse_team(seven)
    with pytest.raises(TeamParseError, match="expected a Pokémon header"):
        parse_team("Ability: Intimidate\nIncineroar")
    with pytest.raises(TeamParseError, match="level must be an integer"):
        parse_team("Rillaboom\nLevel: fifty")
    with pytest.raises(TeamParseError, match="cannot parse stat"):
        parse_team("Rillaboom\nEVs: lots HP")
    with pytest.raises(TeamParseError, match="empty"):
        parse_team("\n\n  \n")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Grassy Glide", "grassyglide"),
        ("Sitrus Berry", "sitrusberry"),
        ("Flare Blitz", "flareblitz"),
        ("Sp. Def", "spdef"),
        ("Urshifu-Rapid-Strike", "urshifurapidstrike"),
        ("U-turn", "uturn"),
    ],
)
def test_to_id(text, expected):
    assert to_id(text) == expected


def test_crlf_and_trailing_spaces():
    clean = fixture("team_sv.txt")
    messy = "\r\n".join(line + "   " for line in clean.splitlines())
    assert parse_team(messy, scale="ev") == parse_team(clean, scale="ev")


def test_unknown_stat_label_and_duplicate():
    team = parse_team("Rillaboom\nEVs: 252 HP / 4 Luck / 8 HP")
    sp = team.members[0].spread
    assert sp is not None and sp.values == {"hp": 8}
    assert any("unknown stat label 'Luck'" in w for w in team.warnings)
    assert any("duplicate stat hp" in w for w in team.warnings)

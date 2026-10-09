import json

import pytest

from mcp_pokemon.models import Spread, Team, TeamMember
from mcp_pokemon.names import NameIndex
from mcp_pokemon.predict import (
    check_score,
    cohesion,
    effective_speed,
    familiarity,
    matchup_component,
    matchup_matrix,
    resolve_team,
    score_components,
    speed_summary,
    team_familiarity,
)
from mcp_pokemon.smogon import ChaosFile, UsageNotFound
from mcp_pokemon.teams import TeamParseError, parse_team

from .conftest import fixture

BASE_SPEED = {
    "Rillaboom": 85,
    "Incineroar": 60,
    "Sneasler": 120,
    "Salamence-Mega": 120,
    "Urshifu-Rapid-Strike": 97,
    "Urshifu-Single-Strike": 97,
    "Pikachu": 90,
}


def chaos() -> ChaosFile:
    return ChaosFile.from_json(
        "2026-09", "gen9championsvgc2026regmc", 1760, json.loads(fixture("chaos_small.json"))
    )


def index() -> NameIndex:
    return NameIndex.from_csv(fixture("pokemon_species.csv"), fixture("pokemon_species_names.csv"))


def member(key: str, **kw) -> TeamMember:
    return TeamMember(species=key, key=key, **kw)


def resolved(text: str) -> Team:
    return resolve_team(chaos(), parse_team(text, scale="points"), index())


def test_check_score():
    c = chaos()
    assert check_score(c, "Sneasler", "Rillaboom") == pytest.approx(0.45 - 4 * 0.085, abs=1e-4)
    assert check_score(c, "Rillaboom", "Incineroar") == pytest.approx(0.16, abs=1e-4)
    assert check_score(c, "Pikachu", "Sneasler") is None


def test_matchup_matrix_sign_and_no_data():
    c = chaos()
    m = matchup_matrix(c, ["Rillaboom", "Pikachu"], ["Incineroar", "Sneasler"])
    ri = m[("Rillaboom", "Incineroar")]
    assert ri.edge == pytest.approx(0.16 - 0.10, abs=1e-4)
    assert ri.a_checks_b == pytest.approx(0.16, abs=1e-4)
    assert not ri.no_data
    assert m[("Pikachu", "Sneasler")].no_data and m[("Pikachu", "Sneasler")].edge == 0
    rev = matchup_matrix(c, ["Incineroar"], ["Rillaboom"])
    assert rev[("Incineroar", "Rillaboom")].edge == pytest.approx(-ri.edge, abs=1e-6)


def test_matchup_component_antisymmetric():
    c = chaos()
    a, b = ["Rillaboom", "Pikachu"], ["Incineroar", "Sneasler"]
    ab = matchup_component(matchup_matrix(c, a, b), a, b)
    ba = matchup_component(matchup_matrix(c, b, a), b, a)
    assert ab == pytest.approx(-ba) and ab != 0
    assert matchup_component({}, [], []) == 0.0


def test_effective_speed():
    jolly = member("Incineroar", nature="Jolly", spread=Spread(scale="ev", values={"spe": 252}))
    assert effective_speed(60, jolly) == 123
    pts = member("Incineroar", nature="Jolly", spread=Spread(scale="points", values={"spe": 32}))
    assert effective_speed(60, pts) == 123
    assert effective_speed(60, member("Incineroar")) == 80
    assert effective_speed(60, member("Incineroar", nature="Brave")) == 72


def test_speed_summary_counts_and_trick_room():
    fast = member("Sneasler", nature="Jolly", spread=Spread(scale="ev", values={"spe": 252}))
    slow = member("Incineroar", nature="Brave")
    mid = member("Rillaboom")
    pika = member("Pikachu", nature="Timid", spread=Spread(scale="ev", values={"spe": 252}))
    summary, comp = speed_summary([fast, slow], [mid, pika], BASE_SPEED)
    # fast (189) > mid (90), fast > pika (156); slow (72) < both
    assert (summary.a_faster, summary.b_faster, summary.ties) == (2, 2, 0)
    assert comp == 0.0 and summary.note is None

    tr = member(  # 189: faster than mid (90) and pika (156)
        "Salamence-Mega",
        nature="Jolly",
        spread=Spread(scale="ev", values={"spe": 252}),
        moves=["Trick Room"],
    )
    summary, comp = speed_summary([fast, tr], [mid, pika], BASE_SPEED)
    assert (summary.a_faster, summary.b_faster) == (4, 0)
    assert comp == 0.5  # 1.0 halved: only one side carries Trick Room
    assert summary.a_speed_control == ["Trick Room"] and summary.b_speed_control == []
    assert "Trick Room" in (summary.note or "")
    both = [mid, member("Pikachu", moves=["Trick Room"])]
    s_both, comp_both = speed_summary([fast, tr], both, BASE_SPEED)
    assert comp_both == 1.0 and s_both.note is None

    summary, comp = speed_summary([fast], [member("Rillaboom", moves=["Tailwind"])], {})
    assert summary.note is not None and "base speed unknown" in summary.note and comp == 0.0

    s2, comp_a = speed_summary([fast], [slow], BASE_SPEED)
    _, comp_b = speed_summary([slow], [fast], BASE_SPEED)
    assert comp_a == 1.0 and comp_b == -1.0 and s2.ties == 0


def test_cohesion():
    c = chaos()
    core = cohesion(c, ["Rillaboom", "Incineroar", "Sneasler"])
    loose = cohesion(c, ["Rillaboom", "Pikachu", "Urshifu-Single-Strike"])
    assert core == pytest.approx(0.381, abs=0.01)
    assert loose == 0.0
    assert cohesion(c, ["Rillaboom"]) == 0.0


def test_familiarity():
    c = chaos()
    rilla = member(
        "Rillaboom",
        item="Miracle Seed",
        ability="Grassy Surge",
        moves=["Grassy Glide", "Fake Out", "Wood Hammer", "U-turn"],
    )
    f = familiarity(c, rilla)
    assert f is not None and f > 0.85
    odd = member("Rillaboom", item="Choice Band", ability="Grassy Surge")
    assert familiarity(c, odd) == pytest.approx((0.0 + 5995 / 6010) / 2, abs=1e-3)
    assert familiarity(c, member("Rillaboom")) is None
    assert team_familiarity(c, [rilla, member("Pikachu")]) == pytest.approx(f, abs=1e-4)


def test_resolve_team_names_and_brought():
    t = resolved("rillaboom\n\n咆哮虎\n\nUrshifu-Rapid-Strike\n\nSalam")
    assert [m.key for m in t.members] == [
        "Rillaboom",
        "Incineroar",
        "Urshifu-Rapid-Strike",
        "Salamence-Mega",
    ]
    assert any("Urshifu-Rapid-Strike: no checks/counters" in w for w in t.warnings)

    team = parse_team("Rillaboom\n\nIncineroar\n\nUrshifu-Rapid-Strike\n\nSneasler")
    team.brought = ["Urshifu", "咆哮虎", "rilla", "Sneasler"]
    t = resolve_team(chaos(), team, index())
    assert t.brought == ["Urshifu-Rapid-Strike", "Incineroar", "Rillaboom", "Sneasler"]

    with pytest.raises(UsageNotFound):
        resolved("Garchomp")
    team.brought = ["Pikachu"]
    with pytest.raises(TeamParseError, match="not on the team"):
        resolve_team(chaos(), team, index())
    team.brought = ["Rillaboom", "rillaboom"]
    with pytest.raises(TeamParseError, match="duplicate"):
        resolve_team(chaos(), team, index())


def test_components_antisymmetric_on_fixture_teams():
    c = chaos()
    sv = resolve_team(c, parse_team(fixture("team_sv.txt"), scale="ev"), index())
    ch = resolve_team(c, parse_team(fixture("team_champions.txt"), scale="points"), index())
    a, b = sv.members[:4], ch.members[2:6]
    ab = score_components(c, a, b, BASE_SPEED)
    ba = score_components(c, b, a, BASE_SPEED)
    assert set(ab.values) == {"matchup", "speed", "cohesion", "familiarity"}
    for k, v in ab.values.items():
        assert v == pytest.approx(-ba.values[k], abs=1e-6), k
    assert ab.speed.a_faster == ba.speed.b_faster
    assert ab.speed.b_speed_control == ["Tailwind"]
    assert len(ab.matrix) == 16
    mirror = score_components(c, a, a, BASE_SPEED)
    assert all(v == 0 for v in mirror.values.values())

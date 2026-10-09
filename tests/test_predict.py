import json
from itertools import combinations

import pytest

from mcp_pokemon.models import Spread, Team, TeamMember
from mcp_pokemon.names import NameIndex
from mcp_pokemon.predict import (
    WEIGHTS,
    check_score,
    choose_bring,
    cohesion,
    effective_speed,
    familiarity,
    matchup_component,
    matchup_matrix,
    predict,
    resolve_team,
    score_components,
    speed_summary,
    team_familiarity,
    threats,
    type_edge_fn,
    type_effectiveness,
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


# ---- type fallback, bring selection, prediction ------------------------------------------

CHART = {
    "electric": {"water": 2.0, "flying": 2.0, "ground": 0.0, "grass": 0.5},
    "fighting": {"normal": 2.0, "steel": 2.0, "ghost": 0.0, "flying": 0.5},
    "water": {"fire": 2.0, "water": 0.5},
    "flying": {"fighting": 2.0, "electric": 0.5},
    "dragon": {"dragon": 2.0},
}
TYPES = {
    "Pikachu": ["electric"],
    "Urshifu-Rapid-Strike": ["fighting", "water"],
    "Salamence-Mega": ["dragon", "flying"],
    "Sneasler": ["fighting", "poison"],
}


def test_type_effectiveness_and_edge():
    assert type_effectiveness(CHART, "electric", ["fighting", "water"]) == 2.0
    assert type_effectiveness(CHART, "electric", ["ground"]) == 0.0
    assert type_effectiveness(CHART, "fire", ["water"]) == 1.0  # unknown attacker → neutral
    edge = type_edge_fn(TYPES, CHART)
    # Pikachu hits Urshifu-RS 2× (+1); Urshifu's best into Electric is neutral (0) → +0.05
    assert edge("Pikachu", "Urshifu-Rapid-Strike") == pytest.approx(0.05)
    assert edge("Urshifu-Rapid-Strike", "Pikachu") == pytest.approx(-0.05)
    # Salamence: Flying 2× on Fighting (+1); Urshifu: Water neutral, Fighting 0.5× → best 0 → +0.05
    assert edge("Salamence-Mega", "Urshifu-Rapid-Strike") == pytest.approx(0.05)
    assert edge("Pikachu", "Rillaboom") is None  # typing unknown


def test_matchup_matrix_type_fallback():
    c = chaos()
    edge = type_edge_fn(TYPES, CHART)
    m = matchup_matrix(c, ["Pikachu"], ["Urshifu-Rapid-Strike", "Sneasler", "Rillaboom"], edge)
    u = m[("Pikachu", "Urshifu-Rapid-Strike")]
    assert u.no_data and u.basis == "types" and u.edge == pytest.approx(0.05)
    # both Fighting-based with nothing super effective either way → 0 but still from types
    assert m[("Pikachu", "Sneasler")].basis == "types"
    r = m[("Pikachu", "Rillaboom")]
    assert r.no_data and r.basis == "none" and r.edge == 0.0
    # checks data wins over the fallback
    with_data = matchup_matrix(c, ["Rillaboom"], ["Incineroar"], edge)[("Rillaboom", "Incineroar")]
    assert with_data.basis == "checks" and not with_data.no_data
    # antisymmetric with the fallback too
    rev = matchup_matrix(c, ["Urshifu-Rapid-Strike"], ["Pikachu"], edge)
    assert rev[("Urshifu-Rapid-Strike", "Pikachu")].edge == pytest.approx(-u.edge)


def champions_team() -> Team:
    return resolve_team(chaos(), parse_team(fixture("team_champions.txt"), scale="points"), index())


def sv_team() -> Team:
    return resolve_team(chaos(), parse_team(fixture("team_sv.txt"), scale="ev"), index())


def test_choose_bring_minimax_and_given():
    c = chaos()
    a, b = champions_team(), sv_team()  # 6 and 4 members
    bring_a, bring_b, plan_a, plan_b = choose_bring(c, a, b, BASE_SPEED)
    assert len(bring_a) == 4 and len(plan_a.pokemon) == 4 and plan_a.source == "minimax"
    assert len(set(plan_a.pokemon)) == 4 and set(plan_a.pokemon) <= {m.key for m in a.members}
    assert plan_b.pokemon == [m.key for m in b.members] and plan_b.source == "minimax"
    assert [m.key for m in bring_b] == plan_b.pokemon

    a.brought = ["Rillaboom", "Incineroar", "Sneasler", "Pikachu"]
    a = resolve_team(c, a, index())
    _, _, plan_a, plan_b = choose_bring(c, a, b, BASE_SPEED)
    assert plan_a.pokemon == a.brought and plan_a.source == "given"
    assert plan_b.source == "best_response"

    # maximin property: no other four of A has a better worst case against B's single four
    a = champions_team()
    _, _, plan_a, _ = choose_bring(c, a, b, BASE_SPEED)

    def sub(members: list[TeamMember]) -> float:
        v = score_components(c, members, b.members, BASE_SPEED).values
        return WEIGHTS["matchup"] * v["matchup"] + WEIGHTS["speed"] * v["speed"]

    best = max(sub(list(four)) for four in combinations(a.members, 4))
    chosen = [m for m in a.members if m.key in plan_a.pokemon]
    assert sub(chosen) == pytest.approx(best)


def test_predict_symmetry_mirror_and_warnings():
    c = chaos()
    a, b = champions_team(), sv_team()
    pab = predict(c, a, b, BASE_SPEED)
    pba = predict(c, b, a, BASE_SPEED)
    assert 0 < pab.win_probability_a < 1
    assert pab.win_probability_a == pytest.approx(1 - pba.win_probability_a, abs=1e-3)
    assert pab.bring_a.pokemon == pba.bring_b.pokemon
    assert pab.bring_b.pokemon == pba.bring_a.pokemon
    for k, v in pab.components.items():
        assert v == pytest.approx(-pba.components[k], abs=1e-6), k
    assert set(pab.components) == set(WEIGHTS)
    assert 1 <= len(pab.key_matchups) <= 5
    assert all(e.basis != "none" for e in pab.key_matchups)
    edges = [abs(e.edge) for e in pab.key_matchups]
    assert edges == sorted(edges, reverse=True)
    assert pab.format_id == "gen9championsvgc2026regmc" and pab.rating == 1760
    # SV paste carries Tera types; the format is Champions → one warning
    assert any("Tera" in w for w in pab.warnings)
    assert any(w.startswith("team A: ") or w.startswith("team B: ") for w in pab.warnings)

    mirror = predict(c, a, a, BASE_SPEED)
    assert mirror.win_probability_a == 0.5
    assert mirror.bring_a.pokemon == mirror.bring_b.pokemon
    assert all(v == 0 for v in mirror.components.values())


def test_predict_respects_given_brought_and_speed_block():
    c = chaos()
    a, b = champions_team(), sv_team()
    a.brought = ["Salamence-Mega", "Sneasler", "Pikachu", "Urshifu-Single-Strike"]
    a = resolve_team(c, a, index())
    p = predict(c, a, b, BASE_SPEED)
    assert p.bring_a.pokemon == a.brought and p.bring_a.source == "given"
    assert p.speed.a_faster + p.speed.b_faster + p.speed.ties == 16
    assert p.speed.b_speed_control == []  # team_sv has no speed control among its four


def test_threats_rank_meta_against_team():
    c = chaos()
    edge = type_edge_fn(TYPES, CHART)
    # Rillaboom checks Incineroar (+0.16 vs 0.10) → it threatens a team of Incineroar + Pikachu
    ts = threats(c, ["Incineroar", "Pikachu"], c.ranking, edge, top_n=3)
    names = [t.pokemon for t in ts]
    assert "Incineroar" not in names and "Pikachu" not in names  # own members excluded
    rilla = next(t for t in ts if t.pokemon == "Rillaboom")
    assert rilla.beats == ["Incineroar"]
    assert rilla.edge == pytest.approx((0.16 - 0.10) / 2, abs=1e-3)
    assert rilla.usage_percent > 0
    assert len(ts) <= 3 and [t.edge for t in ts] == sorted((t.edge for t in ts), reverse=True)
    # a pool member with no data of any kind against the team is skipped entirely
    all_ts = threats(c, ["Incineroar", "Pikachu"], c.ranking, edge, top_n=99)
    assert "Urshifu-Single-Strike" not in [t.pokemon for t in all_ts]
    assert "Salamence-Mega" in [t.pokemon for t in all_ts]  # typing known vs Pikachu

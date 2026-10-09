import json
from pathlib import Path

import httpx
import pytest
import respx

from mcp_pokemon.cache import Http
from mcp_pokemon.deps import Deps

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def deps() -> Deps:
    return Deps.build(Http(httpx.AsyncClient(), retries=1))


@pytest.fixture
def mocked(deps: Deps):
    """Mock every URL the clients hit using small fixtures."""
    base = "https://pokeapi.co/api/v2/"
    csv = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
    with respx.mock(assert_all_called=False) as router:
        router.get(csv + "pokemon_species.csv").respond(text=fixture("pokemon_species.csv"))
        router.get(csv + "pokemon_species_names.csv").respond(
            text=fixture("pokemon_species_names.csv")
        )
        for sid in (25, 892):
            router.get(url__regex=rf"{base}pokemon-species/{sid}/?$").respond(
                json=json.loads(fixture(f"species_{sid}.json"))
            )
        router.get(f"{base}pokemon-species/pikachu").respond(
            json=json.loads(fixture("species_25.json"))
        )
        router.get(f"{base}pokemon-species/urshifu").respond(
            json=json.loads(fixture("species_892.json"))
        )
        router.get(f"{base}pokemon/pikachu").respond(
            json=json.loads(fixture("pokemon_pikachu.json"))
        )
        router.get(f"{base}pokemon/urshifu-single-strike").respond(
            json=json.loads(fixture("pokemon_urshifu-single-strike.json"))
        )
        router.get(f"{base}pokemon/urshifu-rapid-strike").respond(
            json=json.loads(fixture("pokemon_urshifu-rapid-strike.json"))
        )
        for slug in ("rillaboom", "incineroar", "sneasler", "salamence-mega"):
            router.get(f"{base}pokemon/{slug}").respond(
                json=json.loads(fixture(f"pokemon_{slug}.json"))
            )
        router.get(url__regex=rf"{base}pokemon/.*").respond(404)
        router.get(url__regex=rf"{base}type/\d+/?").mock(
            side_effect=lambda req: httpx.Response(
                200,
                json=json.loads(
                    fixture(f"type_{req.url.path.rstrip('/').rsplit('/', 1)[-1]}.json")
                ),
            )
        )
        router.get(url__regex=rf"{base}ability/\d+/?").mock(
            side_effect=lambda req: httpx.Response(
                200,
                json=json.loads(
                    fixture(f"ability_{req.url.path.rstrip('/').rsplit('/', 1)[-1]}.json")
                ),
            )
        )
        router.get(url__regex=rf"{base}pokemon-form/\d+/?").respond(json={"names": []})

        smogon = "https://www.smogon.com/stats/"
        router.get(smogon).respond(text=fixture("smogon_index.html"))
        router.get(smogon + "2026-09/chaos/").respond(text=fixture("smogon_chaos_index.html"))
        router.get(smogon + "2026-08/chaos/").respond(text=fixture("smogon_chaos_index.html"))
        router.get(smogon + "2026-09/chaos/gen9championsvgc2026regmc-1760.json").respond(
            json=json.loads(fixture("chaos_small.json"))
        )
        router.get(smogon + "2026-09/chaos/gen9championsvgc2026regmc-1500.json").respond(
            json=json.loads(fixture("chaos_small.json"))
        )
        yield router

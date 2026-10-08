"""Lazily constructed shared clients (one set per process)."""

from __future__ import annotations

from dataclasses import dataclass

from .cache import Http, TTLCache
from .names import NameIndexLoader
from .pokeapi import PokeApiClient
from .smogon import SmogonClient


@dataclass
class Deps:
    http: Http
    cache: TTLCache
    names: NameIndexLoader
    pokeapi: PokeApiClient
    smogon: SmogonClient

    @classmethod
    def build(cls, http: Http | None = None) -> Deps:
        http = http or Http()
        cache = TTLCache()
        names = NameIndexLoader(http, cache)
        return cls(
            http=http,
            cache=cache,
            names=names,
            pokeapi=PokeApiClient(http, cache, names),
            smogon=SmogonClient(http, cache),
        )


_deps: Deps | None = None


def get_deps() -> Deps:
    global _deps
    if _deps is None:
        _deps = Deps.build()
    return _deps


def set_deps(deps: Deps | None) -> None:
    global _deps
    _deps = deps

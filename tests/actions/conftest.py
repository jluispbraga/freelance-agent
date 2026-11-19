"""Utilitários de teste da Central de Ações (código só de teste)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.actions.policy import Context, Owner, SourceState

NOW = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
OWNER = Owner(user_id=111, chat_id=111)


def make_context(**overrides) -> Context:
    base = dict(
        now=NOW,
        owner=OWNER,
        sources={
            "freelancer": SourceState(compliance="approved", mode="scheduled"),
            "upwork": SourceState(compliance="approved", mode="on_demand"),
            "pendente": SourceState(compliance="pending", mode="scheduled"),
        },
        granted_scopes={"upwork": frozenset({"jobs:read", "profile:read"})},
        budget_reserved=False,
        sandboxed=False,
        approval=None,
    )
    base.update(overrides)
    return Context(**base)


@pytest.fixture
def ctx() -> Context:
    return make_context()


@pytest.fixture
def later():
    return lambda hours: NOW + timedelta(hours=hours)

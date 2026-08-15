"""
F11 Phase 2 — Declarative FlagSpec registry tests.

Six tests verify the invariants of
``utils.flag_registry.FlagSpec`` / :data:`utils.flag_registry.FLAG_REGISTRY`
and the companion :func:`core.feature_flags.is_enabled` resolver.

Run with::

    uv run pytest tests/test_flag_registry.py -q
"""


from typing import cast

import pytest

from hledac.universal._core.feature_flags import is_enabled





    FLAG_REGISTRY,
    FlagGroup,
    VALID_GROUPS,
    FlagRegistryError,
    FlagSpec,
    get_spec,
    list_flags,
    register,
)

# ---------------------------------------------------------------------------

from _core import aclose# Group integrity
# ---------------------------------------------------------------------------

def test_all_registered_flags_have_valid_groups() -> None:
    """Every spec in the registry must declare one of the 8 taxonomy groups.

    Guard against typos like ``"brian"`` or ``"intelligence"`` (missing
    ``_apis``) that would otherwise slip through ``@dataclass`` and
    silently break Phase 3 group-filtered lookups.
    """
    assert FLAG_REGISTRY, "registry must not be empty"
    for name, spec in FLAG_REGISTRY.items():
        assert spec.group in VALID_GROUPS, (
            f"{name}: group {spec.group!r} is not in {sorted(VALID_GROUPS)}"
        )
    # Sanity: at least 20 flags (taxonomy top-N coverage)
    assert len(FLAG_REGISTRY) >= 20, (
        f"expected >= 20 registered flags, found {len(FLAG_REGISTRY)}"
    )


# ---------------------------------------------------------------------------
# Implication graph integrity
# ---------------------------------------------------------------------------

def test_implies_references_known_flags() -> None:
    """Every flag listed in ``spec.implies`` must be registered.

    Implication rules are the substrate for Phase 3 auto-activation
    (e.g. ``DSPY=1`` must imply ``LLM=1``). A dangling reference would
    trigger a ``KeyError`` at validation time — caught here.
    """
    dangling: list[tuple[str, str]] = []
    for name, spec in FLAG_REGISTRY.items():
        for imp in spec.implies:
            if imp not in FLAG_REGISTRY:
                dangling.append((name, imp))
    assert not dangling, (
        f"implies references unknown flags: {dangling}"
    )


# ---------------------------------------------------------------------------
# Conflict symmetry
# ---------------------------------------------------------------------------

def test_conflicts_are_symmetric() -> None:
    """For every (A,B) conflict, B must list A as a conflict too.

    Mutual-exclusion is enforced at sprint startup (Phase 3 fail-fast
    validation). Asymmetric declarations would let a user enable both
    A and B by toggling only B, which the validator would not catch.
    """
    broken: list[tuple[str, str]] = []
    for name, spec in FLAG_REGISTRY.items():
        for other in spec.conflicts_with:
            other_spec = FLAG_REGISTRY.get(other)
            if other_spec is None:
                broken.append((name, other))
                continue
            if name not in other_spec.conflicts_with:
                broken.append((name, other))
    assert not broken, (
        f"asymmetric conflicts: {broken}; "
        f"use _register_symmetric_conflict() helper"
    )


# ---------------------------------------------------------------------------
# is_enabled() — fail-safe default
# ---------------------------------------------------------------------------

def test_is_enabled_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flag that is unset returns ``False`` (fail-safe off)."""
    sentinel = "HLEDAC_ENABLE_TEST_SENTINEL_DEFAULT"
    monkeypatch.delenv(sentinel, raising=False)
    assert is_enabled(sentinel) is False
    # Explicit default="0" matches unset behavior.
    assert is_enabled(sentinel, default="0") is False
    # Caller can opt in to ON-by-default.
    assert is_enabled(sentinel, default="1") is True


# ---------------------------------------------------------------------------
# is_enabled() — env override
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("0", False),
        ("true", True),
        ("false", False),
        ("yes", True),
        ("on", True),
        ("", False),
        ("FALSE", False),  # case-insensitive
    ],
)
def test_is_enabled_env_override(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    """Direct env-var override of :func:`is_enabled` for canonical tokens.

    Truthy: ``1``, ``true``, ``yes``, ``on`` → True.
    Falsey: ``0``, ``false``, ``""`` → False.
    Case-insensitive.
    """
    sentinel = "HLEDAC_ENABLE_TEST_SENTINEL_OVERRIDE"
    monkeypatch.setenv(sentinel, value)
    assert is_enabled(sentinel) is expected


# ---------------------------------------------------------------------------
# Duplicate registration
# ---------------------------------------------------------------------------

def test_registry_no_duplicates() -> None:
    """Re-registering the same flag name raises :class:`FlagRegistryError`.

    A duplicate registration usually means two modules declared the
    same flag with different metadata. Fail loudly at import time
    rather than letting the second ``register()`` overwrite the first.
    """
    # The registry is already populated by import; capture a snapshot
    # of one existing spec and try to re-register a (slightly) different
    # version under the same name.
    first_name, first_spec = next(iter(FLAG_REGISTRY.items()))
    duplicate = FlagSpec(
        name=first_name,
        group=first_spec.group,
        description="intentional duplicate for the test",
    )
    with pytest.raises(FlagRegistryError) as excinfo:
        register(duplicate)
    assert first_name in str(excinfo.value)
    # And the original spec is preserved (no overwrite).
    assert FLAG_REGISTRY[first_name] is first_spec


# ---------------------------------------------------------------------------
# Bonus: discovery helpers + invalid group rejection
# ---------------------------------------------------------------------------

def test_list_flags_and_get_spec() -> None:
    """Discovery helpers must return consistent views of the registry."""
    # No filter → every spec, in insertion order.
    all_specs = list_flags()
    assert len(all_specs) == len(FLAG_REGISTRY)
    # Group filter must yield a subset.
    network = list_flags("network")
    assert network, "expected at least one network/* flag"
    assert all(s.group == "network" for s in network)
    # get_spec round-trips by name.
    sample = network[0]
    assert get_spec(sample.name) is sample
    assert get_spec("HLEDAC_NOT_REGISTERED") is None


def test_register_rejects_invalid_group() -> None:
    """Group typo → :class:`FlagRegistryError` at registration time."""
    # ``cast`` bypasses the Literal type check so we can deliberately
    # feed an invalid group string to the runtime validator.
    # msgspec.Struct doesn't have __dataclass_fields__, use typing.cast instead.
    bad = FlagSpec(
        name="HLEDAC_TEST_BAD_GROUP",
        group=cast(FlagGroup, "brian"),  # type: ignore[arg-type]
    )
    with pytest.raises(FlagRegistryError) as excinfo:
        register(bad)
    assert "invalid group" in str(excinfo.value)
    # And nothing was written to the registry.
    assert "HLEDAC_TEST_BAD_GROUP" not in FLAG_REGISTRY

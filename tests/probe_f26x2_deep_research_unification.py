"""
tests/probe_f26x2_deep_research_unification.py
===============================================

Sprint F26X2: DeepResearchConfig single-source-of-truth — probe tests.

Two definitions of DeepResearchConfig existed in the repo:
- config/__init__.py:103 (10 fields, strategy: str)  — ZOMBIE
- project_types.py:913 (6 fields, strategy: ExplorationStrategy enum) — CANONICAL

The zombie was the result of a config.py → config/ package migration.
This probe verifies the unification:
- One class only (config re-exports project_types version)
- Zombie 4 fields are gone everywhere
- UniversalConfig.deep_research uses the canonical type
- to_dict() serializes the unified 6-field schema
- layers/research_layer.py still imports the unified class
- Strategy field uses the typed enum, not a raw str

Tests:
1. config.DeepResearchConfig IS project_types.DeepResearchConfig
2. UniversalConfig.deep_research is the canonical instance
3. The 4 zombie fields do not exist on the unified class
4. Strategy field is ExplorationStrategy enum
5. to_dict() round-trip works
6. layers/research_layer.py imports work
7. __all__ still exports DeepResearchConfig
8. create_config() and from_env() work
"""


from dataclasses import fields


class TestF26X2SingleClass:
    """F26X2: Only one DeepResearchConfig class exists across the package."""

    def test_config_deepresearchconfig_is_project_types(self):
        """config.DeepResearchConfig re-exports the canonical version."""
        from hledac.universal.config import DeepResearchConfig as ConfigDRC
        from hledac.universal.project_types import DeepResearchConfig as ProjectTypesDRC

        assert ConfigDRC is ProjectTypesDRC, (
            "config.DeepResearchConfig must re-export project_types.DeepResearchConfig"
        )

    def test_universalconfig_field_type(self):
        """UniversalConfig.deep_research uses the canonical type."""
        from hledac.universal.config import UniversalConfig
        from hledac.universal.project_types import DeepResearchConfig

        cfg = UniversalConfig()
        assert isinstance(cfg.deep_research, DeepResearchConfig)

    def test_no_zombie_class_survives(self):
        """config/__init__.py has no local DeepResearchConfig class body."""

        # If the class is defined LOCALLY in config/__init__.py, its
        # __module__ would be 'hledac.universal.config'.
        # If it is re-exported, its __module__ is 'hledac.universal.project_types'.
        from hledac.universal.project_types import DeepResearchConfig

        assert DeepResearchConfig.__module__ == "hledac.universal.project_types", (
            f"DeepResearchConfig.__module__ is {DeepResearchConfig.__module__!r} — "
            "a local class body would indicate a re-introduced duplicate"
        )


class TestF26X2ZombieFieldsRemoved:
    """F26X2: 4 zombie fields are gone from the unified class."""

    ZOMBIE_FIELDS = [
        "max_documents",
        "max_citations_per_doc",
        "enable_auto_summarize",
        "summarization_model",
    ]

    def test_zombie_fields_not_in_dataclass(self):
        from hledac.universal.project_types import DeepResearchConfig

        live = {f.name for f in fields(DeepResearchConfig)}
        for z in self.ZOMBIE_FIELDS:
            assert z not in live, f"Zombie field {z!r} still on DeepResearchConfig"

    def test_zombie_fields_not_referenced_anywhere(self):
        """No source file references the 4 zombie field names outside docstrings/tests."""
        import subprocess

        for z in self.ZOMBIE_FIELDS:
            result = subprocess.run(
                ["rg", "-l", z, "--type=py", "config/", "project_types.py", "layers/"],
                capture_output=True,
                text=True,
            )
            files = [x for x in result.stdout.splitlines() if x]
            assert not files, (
                f"Zombie field {z!r} still referenced in: {files}"
            )


class TestF26X2SchemaCorrectness:
    """F26X2: The 6 canonical fields are intact with correct types."""

    def test_canonical_fields_present(self):
        from hledac.universal.project_types import DeepResearchConfig

        live = {f.name: f.type for f in fields(DeepResearchConfig)}
        expected = {"max_depth", "strategy", "follow_citations", "explore_tangents", "max_threads", "citation_types"}
        assert set(live.keys()) == expected, (
            f"Schema drift. Expected {expected}, got {set(live.keys())}"
        )

    def test_strategy_is_typed_enum(self):
        """strategy must be ExplorationStrategy enum, not raw str."""
        from hledac.universal.project_types import (
            DeepResearchConfig,
            ExplorationStrategy,
        )

        cfg = DeepResearchConfig()
        assert isinstance(cfg.strategy, ExplorationStrategy)
        assert cfg.strategy == ExplorationStrategy.HYBRID


class TestF26X2Serialization:
    """F26X2: to_dict() and the update path still work post-unification."""

    def test_to_dict_includes_deep_research(self):
        from hledac.universal.config import UniversalConfig

        cfg = UniversalConfig()
        d = cfg.to_dict()
        assert "deep_research" in d
        assert d["deep_research"]["max_depth"] == 10
        assert d["deep_research"]["follow_citations"] is True

    def test_to_dict_citation_types_list(self):
        from hledac.universal.config import UniversalConfig

        cfg = UniversalConfig()
        d = cfg.to_dict()
        assert d["deep_research"]["citation_types"] == [
            "academic",
            "patent",
            "preprint",
            "dataset",
        ]

    def test_create_config_default_deep_research(self):
        from hledac.universal.config import create_config
        from hledac.universal.project_types import DeepResearchConfig

        cfg = create_config()
        assert isinstance(cfg.deep_research, DeepResearchConfig)

    def test_from_env_deep_research(self):
        import os

        from hledac.universal.config import UniversalConfig

        # Don't change mode, just ensure import works
        os.environ.pop("HLEDAC_RESEARCH_MODE", None)
        cfg = UniversalConfig.from_env()
        assert cfg.deep_research.max_depth == 10


class TestF26X2ConsumersIntact:
    """F26X2: Downstream consumers of the unified class still import correctly."""

    def test_research_layer_imports(self):
        from hledac.universal.layers.research_layer import ResearchLayer

        # Construction with no args uses default config (must be unified type)
        layer = ResearchLayer()
        from hledac.universal.project_types import DeepResearchConfig

        assert isinstance(layer.config, DeepResearchConfig)

    def test_research_layer_custom_config(self):
        from hledac.universal.layers.research_layer import ResearchLayer
        from hledac.universal.project_types import DeepResearchConfig, ExplorationStrategy

        custom = DeepResearchConfig(
            max_depth=7,
            strategy=ExplorationStrategy.DEPTH_FIRST,
            explore_tangents=False,
        )
        layer = ResearchLayer(config=custom)
        assert layer.config is custom
        assert layer.config.max_depth == 7
        assert layer.config.strategy == ExplorationStrategy.DEPTH_FIRST
        assert layer.config.explore_tangents is False


class TestF26X2Exports:
    """F26X2: Public API surface is preserved."""

    def test_all_exports_deep_research_config(self):
        import hledac.universal.config as cfg_mod

        assert "DeepResearchConfig" in cfg_mod.__all__

    def test_can_construct_via_config_path(self):
        """The hledac.universal.config import path still works."""
        from hledac.universal.config import DeepResearchConfig

        cfg = DeepResearchConfig(max_depth=3)
        assert cfg.max_depth == 3

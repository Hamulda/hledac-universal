"""
Testy pro validaci tool schémat.
"""
from unittest.mock import Mock

import pytest
from hledac.universal.tool_registry import ToolRegistry
from pydantic import BaseModel, Field, ValidationError


class WebSearchArgs(BaseModel):
    """Schéma argumentů pro web_search nástroj."""
    query: str = Field(..., min_length=1, max_length=1000)
    max_results: int = Field(default=10, ge=1, le=100)
    language: str | None = Field(default="en", pattern="^[a-z]{2}$")


class EntityExtractArgs(BaseModel):
    """Schéma argumentů pro entity_extract nástroj."""
    text: str = Field(..., min_length=1)
    entity_types: list[str] = Field(default=["PERSON", "ORG", "LOC"])
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class TestToolSchemaValidation:
    """Testy pro validaci tool schémat."""

    @pytest.fixture
    def registry(self):
        """Vytvoří ToolRegistry s testovacími nástroji."""
        reg = ToolRegistry()

        # Registruj nástroje se schématy
        reg.register_with_schema(
            name="web_search",
            handler=lambda **kwargs: {"results": []},
            schema=WebSearchArgs
        )
        reg.register_with_schema(
            name="entity_extract",
            handler=lambda **kwargs: {"entities": []},
            schema=EntityExtractArgs
        )

        return reg

    def test_valid_args_pass(self, registry):
        """Validní argumenty projdou validací."""
        # Nemělo by vyhodit výjimku
        registry.validate_args("web_search", {"query": "test", "max_results": 5})
        registry.validate_args("entity_extract", {"text": "Hello world"})

    def test_invalid_args_fail_fast(self, registry):
        """Nevalidní argumenty → fail fast s ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            registry.validate_args("web_search", {"invalid_arg": "value"})

        assert "query" in str(exc_info.value) or "extra_forbidden" in str(exc_info.value)

    def test_missing_required_field(self, registry):
        """Chybějící povinné pole → ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            registry.validate_args("web_search", {"max_results": 5})

        assert "query" in str(exc_info.value)

    def test_type_validation(self, registry):
        """Špatný typ → ValidationError."""
        with pytest.raises(ValidationError):
            registry.validate_args("web_search", {"query": 123})  # int místo str

    def test_range_validation(self, registry):
        """Hodnota mimo rozsah → ValidationError."""
        with pytest.raises(ValidationError):
            registry.validate_args("web_search", {
                "query": "test",
                "max_results": 1000  # Mimo rozsah 1-100
            })

    def test_pattern_validation(self, registry):
        """Nevalidní pattern → ValidationError."""
        with pytest.raises(ValidationError):
            registry.validate_args("web_search", {
                "query": "test",
                "language": "english"  # Mělo by být "en"
            })

    def test_unknown_tool_fails(self, registry):
        """Validace neexistujícího nástroje → fail."""
        with pytest.raises(KeyError):
            registry.validate_args("unknown_tool", {"arg": "value"})


class TestToolExecutionFlow:
    """Testy pro celé flow: validace → spuštění → evidence."""

    @pytest.fixture
    def orchestrator(self):
        """Vytvoří orchestrátor s mock závislostmi."""
        hermes = Mock()
        hermes.generate = Mock(return_value={
            "tool_calls": [{"tool": "web_search", "args": {"query": "test"}}],
            "should_continue": False,
            "final_answer": "Done"
        })

        registry = ToolRegistry()
        registry.register_with_schema(
            name="web_search",
            handler=lambda **kwargs: {"results": ["doc1", "doc2"]},
            schema=WebSearchArgs
        )

        return FullyAutonomousOrchestrator(
            hermes=hermes,
            tools=registry
        )

    @pytest.mark.asyncio
    async def test_valid_plan_executes(self, orchestrator):
        """Valid plan → tool executes → evidence event."""
        await orchestrator.research("test query")

        # Tool by měl být volán
        evidence = orchestrator.evidence_log.get_all()
        tool_results = [e for e in evidence if e.type == "tool_result"]

        assert len(tool_results) >= 1
        assert tool_results[0].data.get("results") == ["doc1", "doc2"]

    @pytest.mark.asyncio
    async def test_invalid_args_blocked(self, orchestrator):
        """Nevalidní argumenty → tool se nezavolá, chyba v evidenci."""
        orchestrator.hermes.generate = Mock(return_value={
            "tool_calls": [{"tool": "web_search", "args": {"max_results": 1000}}],
            "should_continue": False,
            "final_answer": "Done"
        })

        await orchestrator.research("test query")

        # Chyba by měla být zaznamenána
        errors = [e for e in orchestrator.evidence_log.get_all() if e.type == "error"]
        assert len(errors) >= 1
        assert any("max_results" in str(e.data) for e in errors)


class TestSchemaDefinition:
    """Testy pro definici schémat nástrojů."""

    def test_tool_schema_creation(self):
        """Vytvoření ToolSchema objektu."""
        schema = ToolSchema(
            name="test_tool",
            description="Testovací nástroj",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        )

        assert schema.name == "test_tool"
        assert "query" in schema.parameters["properties"]
        assert "limit" in schema.parameters["properties"]

    def test_tool_definition_creation(self):
        """Vytvoření ToolDefinition objektu."""
        handler = Mock(return_value={"result": "ok"})

        tool_def = ToolDefinition(
            name="test_tool",
            handler=handler,
            schema=WebSearchArgs,
            description="Test tool"
        )

        assert tool_def.name == "test_tool"
        assert tool_def.handler == handler
        assert tool_def.schema == WebSearchArgs

    def test_json_schema_generation(self):
        """Generování JSON schématu z Pydantic modelu."""
        schema = WebSearchArgs.model_json_schema()

        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["properties"]["query"]["type"] == "string"
        assert "max_results" in schema["properties"]


class TestSchemaEdgeCases:
    """Edge case testy pro schémata."""

    def test_empty_args_for_optional_only(self, registry):
        """Prázdné argumenty když všechny jsou optional."""
        class AllOptional(BaseModel):
            opt1: str | None = None
            opt2: int | None = Field(default=10)

        reg = ToolRegistry()
        reg.register_with_schema(
            name="all_optional",
            handler=lambda **kwargs: {"ok": True},
            schema=AllOptional
        )

        # Nemělo by vyhodit výjimku
        reg.validate_args("all_optional", {})

    def test_nested_model_validation(self):
        """Validace vnořených modelů."""
        class Address(BaseModel):
            street: str
            city: str

        class Person(BaseModel):
            name: str
            address: Address

        reg = ToolRegistry()
        reg.register_with_schema(
            name="create_person",
            handler=lambda **kwargs: {"created": True},
            schema=Person
        )

        # Validní vnořený model
        reg.validate_args("create_person", {
            "name": "John",
            "address": {"street": "Main St", "city": "NYC"}
        })

        # Nevalidní vnořený model
        with pytest.raises(ValidationError):
            reg.validate_args("create_person", {
                "name": "John",
                "address": {"street": "Main St"}  # Chybí city
            })

    def test_list_validation(self):
        """Validace listů."""
        class WithList(BaseModel):
            items: list[str] = Field(..., min_length=1, max_length=10)

        reg = ToolRegistry()
        reg.register_with_schema(
            name="with_list",
            handler=lambda **kwargs: {"ok": True},
            schema=WithList
        )

        # Validní
        reg.validate_args("with_list", {"items": ["a", "b"]})

        # Příliš mnoho položek
        with pytest.raises(ValidationError):
            reg.validate_args("with_list", {"items": ["x"] * 20})

        # Prázdný list
        with pytest.raises(ValidationError):
            reg.validate_args("with_list", {"items": []})

    def test_union_types(self):
        """Validace union typů."""

        class WithUnion(BaseModel):
            value: str | int

        reg = ToolRegistry()
        reg.register_with_schema(
            name="with_union",
            handler=lambda **kwargs: {"ok": True},
            schema=WithUnion
        )

        # Validní: string
        reg.validate_args("with_union", {"value": "test"})

        # Validní: int
        reg.validate_args("with_union", {"value": 42})

        # Nevalidní: list
        with pytest.raises(ValidationError):
            reg.validate_args("with_union", {"value": [1, 2, 3]})


class TestSchemaDocumentation:
    """Testy pro dokumentaci schémat."""

    def test_description_extraction(self):
        """Extrakce popisu z Pydantic modelu."""
        class DocumentedArgs(BaseModel):
            """Argumenty s dokumentací."""
            query: str = Field(
                ...,
                description="Vyhledávací dotaz",
                examples=["python tutorial"]
            )

        reg = ToolRegistry()
        reg.register_with_schema(
            name="documented",
            handler=lambda **kwargs: {"ok": True},
            schema=DocumentedArgs
        )

        schema = reg.get_schema("documented")
        assert "query" in schema["properties"]

    def test_examples_in_schema(self):
        """Příklady v JSON schématu."""
        class WithExamples(BaseModel):
            query: str = Field(..., examples=["example1", "example2"])

        schema = WithExamples.model_json_schema()

        assert "examples" in schema["properties"]["query"]

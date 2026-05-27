"""Tests for ToolRegistry."""
import pytest
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ToolSchema


@pytest.mark.asyncio
async def test_register_and_dispatch() -> None:
    registry = ToolRegistry()

    @registry.register(description="Add two numbers", parameters_schema={"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]})
    async def add(a: str, b: str) -> str:
        return str(int(a) + int(b))

    result = await registry.dispatch("add", '{"a": "3", "b": "4"}')
    assert result == "7"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_returns_error_json() -> None:
    registry = ToolRegistry()
    result = await registry.dispatch("nonexistent", "{}")
    assert "error" in result


@pytest.mark.asyncio
async def test_dispatch_invalid_json_returns_error() -> None:
    registry = ToolRegistry()

    @registry.register(description="Dummy", parameters_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    async def dummy(x: str) -> str:
        return x

    result = await registry.dispatch("dummy", "not json")
    assert "error" in result


def test_get_schemas_returns_tool_schemas() -> None:
    registry = ToolRegistry()

    @registry.register(description="Test tool", parameters_schema={"type": "object", "properties": {"param": {"type": "string"}}, "required": ["param"]})
    async def my_tool(param: str) -> str:
        return param

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    assert isinstance(schemas[0], ToolSchema)
    assert schemas[0].function.name == "my_tool"
    assert schemas[0].function.description == "Test tool"


def test_get_schemas_filtered_by_names() -> None:
    registry = ToolRegistry()

    @registry.register(description="Tool A", parameters_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    async def tool_a(x: str) -> str:
        return x

    @registry.register(description="Tool B", parameters_schema={"type": "object", "properties": {"y": {"type": "string"}}, "required": ["y"]})
    async def tool_b(y: str) -> str:
        return y

    schemas = registry.get_schemas(names=["tool_a"])
    assert len(schemas) == 1
    assert schemas[0].function.name == "tool_a"


def test_get_tool_returns_entry() -> None:
    registry = ToolRegistry()

    @registry.register(description="Hello tool", parameters_schema={"type": "object", "properties": {}})
    async def hello() -> str:
        return "hello"

    entry = registry.get_tool("hello")
    assert entry is not None
    assert entry.name == "hello"


def test_get_tool_missing_returns_none() -> None:
    registry = ToolRegistry()
    assert registry.get_tool("missing") is None


@pytest.mark.asyncio
async def test_pre_hook_called() -> None:
    calls: list[str] = []

    registry = ToolRegistry()

    @registry.register(description="Hookable tool", parameters_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})
    async def hookable(x: str) -> str:
        return x

    entry = registry.get_tool("hookable")
    assert entry is not None
    entry.pre_hook = lambda x: calls.append(f"pre:{x}")

    await registry.dispatch("hookable", '{"x": "test"}')
    assert calls == ["pre:test"]
"""Real official MCP v2 in-process protocol test; skipped only if SDK absent."""
import asyncio
import json
import pytest


def test_official_sdk_v2_in_memory_client(tmp_path):
    from importlib.metadata import PackageNotFoundError, version
    try:
        installed_version = version("mcp")
    except PackageNotFoundError:
        pytest.skip("Official MCP SDK distribution absent; install .[mcp]")
    if int(installed_version.split(".", 1)[0]) != 2:
        pytest.fail(f"MCP v2 required; installed {installed_version}")
    try:
        from mcp import Client
    except ImportError:
        pytest.fail("MCP SDK distribution installed but public v2 Client unavailable")
    from decisionbound.mcp_server import create_mcp_server
    from decisionbound.identity import init_project
    from decisionbound.storage import SQLiteRepository

    root = tmp_path / "project"
    root.mkdir()
    (root / "example.py").write_text("def smoke_test(): pass\n")
    store = SQLiteRepository(tmp_path / "state.sqlite3")
    init_project(root, store)
    server = create_mcp_server(root, store)

    async def check():
        async with Client(server) as client:
            offered = await client.list_tools()
            assert "project_info" in {t.name for t in offered.tools}
            result = await client.call_tool("search_project", {"query": "smoke_test"})
            assert not result.is_error
            # The official SDK may serialize a dict tool result as JSON text
            # while leaving the optional structured-content field unset.
            raw = result.structured_content
            if raw is None:
                text = [part.text for part in result.content if part.type == "text"]
                assert len(text) == 1
                raw = json.loads(text[0])
            assert raw["query"] == "smoke_test"
            assert raw["results"][0]["file"] == "example.py"
            assert raw["results"][0]["line"] == 1
            assert "smoke_test" in raw["results"][0]["excerpt"]
    asyncio.run(check())


def test_official_sdk_v2_error_text_reaches_the_client(tmp_path):
    """The SDK hides every error but a ToolError, so the next-step hint must arrive on the wire."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        installed_version = version("mcp")
    except PackageNotFoundError:
        pytest.skip("Official MCP SDK distribution absent, install .[mcp]")
    if int(installed_version.split(".", 1)[0]) != 2:
        pytest.fail(f"MCP v2 required, installed {installed_version}")
    from mcp import Client
    from decisionbound.mcp_server import create_mcp_server
    from decisionbound.identity import init_project
    from decisionbound.storage import SQLiteRepository

    root = tmp_path / "project"
    root.mkdir()
    (root / "example.py").write_text("x = 1\n")
    store = SQLiteRepository(tmp_path / "state.sqlite3")
    init_project(root, store)
    server = create_mcp_server(root, store)

    async def check():
        async with Client(server) as client:
            for tool, args, hint in (
                    ("read_file", {"path": "missing.py"}, "Call search_project"),
                    ("search_project", {"query": "a"}, "2 to 120 characters"),
                    ("prepare_context", {"task": "x", "max_tokens": 5}, "100 to 20000")):
                result = await client.call_tool(tool, args)
                assert result.is_error
                text = " ".join(part.text for part in result.content if part.type == "text")
                assert hint in text, text
                assert str(tmp_path) not in text
    asyncio.run(check())

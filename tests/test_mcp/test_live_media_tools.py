"""An existing MCP process discovers feature changes without re-importing."""

import pytest

from superlocalmemory.mcp.http_transport import SLMFastMCP


@pytest.mark.asyncio
async def test_media_tools_refresh_while_the_same_server_is_running(media_enabled):
    from superlocalmemory.runtimes import features

    features.disable_media()
    server = SLMFastMCP("test")
    server.configure_tool_surface(frozenset({"recall", "get_media"}))

    @server.tool()
    def recall():
        return {}

    @server.tool()
    def get_media():
        return {"error": "Turn images on with slm media enable."}

    assert [t.name for t in await server.list_tools()] == ["recall"]
    features.enable_media(source="api", start_install=False)
    assert {t.name for t in await server.list_tools()} == {"recall", "get_media"}
    features.disable_media()
    assert [t.name for t in await server.list_tools()] == ["recall"]
    result = await server.call_tool("get_media", {})
    assert "slm media enable" in result.content[0].text


@pytest.mark.asyncio
async def test_explicit_small_allowlist_does_not_gain_media_tools(media_enabled):
    server = SLMFastMCP("test")
    server.configure_tool_surface(frozenset({"recall"}))

    @server.tool()
    def get_media():
        return {}

    assert await server.list_tools() == []
    with pytest.raises(ValueError, match="not enabled"):
        await server.call_tool("get_media", {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ("remember_media", "remember_document", "media_status", "get_media"),
)
async def test_hidden_media_tools_keep_their_remote_refusal(name):
    from superlocalmemory.mcp import tools_media
    from superlocalmemory.mcp.remote_caller import remote_caller
    from superlocalmemory.runtimes import features

    assert not features.media_enabled()
    server = SLMFastMCP("test")
    tools_media.register_media_tools(server)
    tools_media.register_document_tools(server)
    assert await server.list_tools() == []
    args = {
        "get_media": {"media_id": "a" * 32},
        "media_status": {"job_id": "b" * 32},
    }.get(name, {})
    with remote_caller("rk_00000001"):
        result = await server.call_tool(name, args)
    assert "not available to remote apps" in result.content[0].text


@pytest.mark.asyncio
async def test_hidden_upload_link_keeps_its_opt_in_refusal():
    from superlocalmemory.mcp.remote_caller import remote_caller
    from superlocalmemory.mcp.tools_media_upload import register_upload_link_tool
    from superlocalmemory.runtimes import features

    assert not features.media_enabled()
    server = SLMFastMCP("test")
    register_upload_link_tool(server)
    assert await server.list_tools() == []
    with remote_caller("rk_00000001"):
        result = await server.call_tool("media_upload_link", {})
    assert "not_for_remote" in result.content[0].text
    assert "not allowed to add pictures or documents" in result.content[0].text

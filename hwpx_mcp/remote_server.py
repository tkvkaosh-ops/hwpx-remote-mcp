"""Minimal vendor-neutral Remote MCP entrypoint for HWPX workflows."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from hwpx_mcp.config import get_config
from hwpx_mcp.tools.remote_documents import (
    build_download_router,
    register_remote_document_tools,
)

logger = logging.getLogger("hwpx-remote-mcp")

mcp = FastMCP(
    name="HWPX Remote MCP",
    instructions=(
        "Create native HWPX documents, inspect uploaded HWPX templates, and fill "
        "templates while preserving their package parts and formatting."
    ),
    dependencies=["mcp>=1.30,<2", "python-hwpx>=1.9,<7"],
)
register_remote_document_tools(mcp)


def build_http_app() -> FastAPI:
    """Build the Streamable HTTP app with download and health routes."""
    config = get_config()
    mcp.settings.streamable_http_path = config.path or "/mcp"
    mcp.settings.stateless_http = config.stateless
    mcp.settings.json_response = config.json_response
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="HWPX Remote MCP",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(build_download_router())
    app.mount("/", mcp_app)
    return app


def main() -> None:
    """Run the dedicated Remote MCP server."""
    config = get_config()
    if config.transport == "stdio":
        mcp.run(transport="stdio")
        return
    if config.transport not in ("http", "streamable-http"):
        logger.error("Remote server requires stdio or streamable-http transport")
        sys.exit(1)

    import uvicorn

    uvicorn.run(
        build_http_app(),
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

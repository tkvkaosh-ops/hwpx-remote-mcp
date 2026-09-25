#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HWP MCP Server Configuration Module

Handles environment variable configuration for the MCP server,
supporting multiple transport modes (stdio, http, sse, streamable-http).
"""

import os
from dataclasses import dataclass, field
from typing import Literal

TransportType = Literal["stdio", "http", "sse", "streamable-http"]


@dataclass
class ServerConfig:
    """Server configuration loaded from environment variables.

    Environment Variables:
        MCP_TRANSPORT: Transport type (stdio, http, sse, streamable-http). Default: stdio
        MCP_HOST: Host to bind to for HTTP transports. Default: 0.0.0.0
        MCP_PORT: Port to bind to for HTTP transports. Default: 8000
        MCP_STATELESS: Enable stateless HTTP mode. Default: false
        MCP_JSON_RESPONSE: Use JSON responses instead of SSE streaming. Default: false
        MCP_PATH: URL path for HTTP endpoints. Default: /mcp
    """

    transport: TransportType = field(
        default_factory=lambda: os.getenv("MCP_TRANSPORT", "stdio")
    )
    host: str = field(default_factory=lambda: os.getenv("MCP_HOST", "0.0.0.0"))
    port: int = field(
        default_factory=lambda: int(os.getenv("PORT") or os.getenv("MCP_PORT", "8000"))
    )
    stateless: bool = field(
        default_factory=lambda: os.getenv("MCP_STATELESS", "false").lower() == "true"
    )
    json_response: bool = field(
        default_factory=lambda: os.getenv("MCP_JSON_RESPONSE", "false").lower()
        == "true"
    )
    path: str = field(default_factory=lambda: os.getenv("MCP_PATH", "/mcp"))

    def __post_init__(self):
        """Validate configuration after initialization."""
        valid_transports = ("stdio", "http", "sse", "streamable-http")
        if self.transport not in valid_transports:
            raise ValueError(
                f"Invalid MCP_TRANSPORT: '{self.transport}'. "
                f"Must be one of: {', '.join(valid_transports)}"
            )

        if self.port < 1 or self.port > 65535:
            raise ValueError(
                f"Invalid MCP_PORT: {self.port}. Must be between 1 and 65535."
            )

    def is_http_transport(self) -> bool:
        """Check if the configured transport is HTTP-based."""
        return self.transport in ("http", "sse", "streamable-http")

    def get_run_kwargs(self) -> dict:
        """Get kwargs for mcp.run() based on configuration.

        Returns:
            dict: Keyword arguments to pass to FastMCP.run()
        """
        if self.transport == "stdio":
            return {"transport": "stdio"}

        return {
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "path": self.path,
        }

    def __str__(self) -> str:
        """Return a human-readable configuration summary."""
        if self.transport == "stdio":
            return "Transport: stdio (local)"
        return (
            f"Transport: {self.transport} | "
            f"Address: {self.host}:{self.port}{self.path} | "
            f"Stateless: {self.stateless}"
        )


def get_config() -> ServerConfig:
    """Get the server configuration from environment variables.

    Returns:
        ServerConfig: The loaded configuration

    Raises:
        ValueError: If configuration is invalid
    """
    return ServerConfig()

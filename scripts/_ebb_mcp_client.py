"""Client MCP remoto per il server baba-credits in modalità HTTP+SSE.

Legge da env (carica .env.local automaticamente):
    EBB_MCP_URL    - es. https://38.242.234.47/sse
    EBB_MCP_BEARER - token bearer

Espone una RemoteMcp class con .call(tool, args) async.
Usa httpx con SSL verify disattivato (cert self-signed su IP).
"""
from __future__ import annotations
import os
import ssl
import json
import pathlib
from typing import Any
from contextlib import asynccontextmanager

import httpx
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession


def _load_dotenv_local() -> None:
    """Mini parser per .env.local (no quoting/escapes complessi)."""
    p = pathlib.Path(__file__).resolve().parent.parent / ".env.local"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv_local()


def _config() -> tuple[str, dict[str, str]]:
    url = os.environ.get("EBB_MCP_URL")
    token = os.environ.get("EBB_MCP_BEARER")
    if not url or not token:
        raise RuntimeError("missing EBB_MCP_URL or EBB_MCP_BEARER in env / .env.local")
    headers = {"Authorization": f"Bearer {token}"}
    return url, headers


@asynccontextmanager
async def open_session():
    url, headers = _config()

    def _factory(headers=None, timeout=None, auth=None):
        # cert self-signed su IP: verify=False
        return httpx.AsyncClient(
            headers=headers, timeout=timeout, auth=auth,
            verify=False, follow_redirects=True,
        )

    async with sse_client(url, headers=headers, httpx_client_factory=_factory) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _result_to_dict(result: Any) -> Any:
    """call_tool ritorna un CallToolResult con .content (lista TextContent).
    I tool del server baba-credits ritornano un singolo TextContent JSON."""
    if hasattr(result, "content"):
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    return result


async def call(session: ClientSession, name: str, args: dict) -> dict:
    res = await session.call_tool(name, args)
    return _result_to_dict(res)

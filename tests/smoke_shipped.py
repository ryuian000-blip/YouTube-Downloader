"""Drive the SHIPPED ytdl-agent executable as an MCP server.

The whole point of building a second console binary is that someone who
downloaded the app -- with no Python, no checkout, no virtualenv -- can
register it with Claude Code. This proves that end to end against the
built artifact, not against the source tree.

Run after a build:
    .venv/Scripts/python.exe tests/smoke_shipped.py
    .venv/Scripts/python.exe tests/smoke_shipped.py --network
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def agent_executable() -> Path:
    """The built console binary, on either platform's layout."""
    candidates = [
        REPO_ROOT / "dist" / "YouTube Downloader" / "ytdl-agent.exe",
        REPO_ROOT / "dist" / "YouTube Downloader" / "ytdl-agent",
        REPO_ROOT / "dist" / "YouTube Downloader.app" / "Contents" / "MacOS" / "ytdl-agent",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit(
        "No built ytdl-agent found. Run a build first (build.bat / build.command)."
    )


async def main(with_network: bool) -> int:
    exe = agent_executable()
    print(f"testing shipped binary: {exe}")

    env = dict(os.environ)
    # Never touch the developer's real settings from a smoke run.
    env["YTDL_SETTINGS_FILE"] = str(
        Path(tempfile.gettempdir()) / "ytdl-shipped-smoke-settings.json"
    )
    Path(env["YTDL_SETTINGS_FILE"]).unlink(missing_ok=True)

    params = StdioServerParameters(command=str(exe), args=["mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected: {init.server_info.name} v{init.server_info.version}")

            tools = {t.name for t in (await session.list_tools()).tools}
            print(f"tools: {len(tools)}")
            if "download_video_tool" not in tools:
                print("FAIL: expected tools missing")
                return 1

            setup = await session.call_tool("check_setup_tool", {})
            payload = setup.structured_content or {}
            print(f"check_setup -> ready={payload.get('ready')} "
                  f"yt_dlp={payload.get('yt_dlp_version')}")
            if not payload.get("ready"):
                print(f"FAIL: shipped binary reports it isn't ready: {payload}")
                return 1

            # The bundled binaries must be found from inside the bundle --
            # this is what proves no separate ffmpeg/deno install is needed.
            if not payload.get("ffmpeg") or not payload.get("js_runtime"):
                print("FAIL: shipped binary didn't find its own bundled ffmpeg/deno")
                return 1
            print("OK: found its own bundled ffmpeg + deno")

            gate = await session.call_tool(
                "download_video_tool", {"url": "https://youtu.be/dQw4w9WgXcQ"}
            )
            gate_payload = gate.structured_content or {}
            if not gate_payload.get("needs_confirmation"):
                print(f"FAIL: first download should ask first, got {gate_payload}")
                return 1
            print(f"OK: first download asks -> {gate_payload['ask_the_user'][:70]}…")

            if with_network:
                found = await session.call_tool(
                    "search_youtube_tool", {"query": "anthropic claude", "limit": 1}
                )
                results = (found.structured_content or {}).get("results", [])
                if not results:
                    print("FAIL: search returned nothing")
                    return 1
                print(f"OK: real search works -> {results[0]['title'][:50]}")

            print("\nSHIPPED BINARY OK -- usable with no Python installed")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--network" in sys.argv)))

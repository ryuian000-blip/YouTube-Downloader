"""Drive ytdl_mcp.py over real stdio, the way an MCP client does.

Not named test_*.py on purpose: it spawns a subprocess and (for the
--network run) hits YouTube, so pytest doesn't collect it. Run by hand:

    .venv/Scripts/python.exe tests/smoke_mcp.py            # handshake + tool list
    .venv/Scripts/python.exe tests/smoke_mcp.py --network  # also call real tools
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

EXPECTED_TOOLS = {
    "get_settings_tool",
    "search_youtube_tool",
    "get_video_info_tool",
    "get_transcript_tool",
    "extract_frames_tool",
    "download_video_tool",
    "check_setup_tool",
}


async def main(with_network: bool) -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(REPO_ROOT / "ytdl_mcp.py")],
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected to: {init.server_info.name} v{init.server_info.version}")

            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            print(f"tools exposed: {sorted(names)}")
            missing = EXPECTED_TOOLS - names
            if missing:
                print(f"FAIL: missing tools {sorted(missing)}")
                return 1
            for tool in listed.tools:
                if not (tool.description or "").strip():
                    print(f"FAIL: {tool.name} has no description")
                    return 1
            print("OK: every expected tool is present and documented")

            result = await session.call_tool("check_setup_tool", {})
            payload = result.structured_content or {}
            print(f"check_setup -> ready={payload.get('ready')} "
                  f"yt_dlp={payload.get('yt_dlp_version')}")
            if not payload.get("ready"):
                print(f"FAIL: setup not ready: {payload}")
                return 1

            # An unreachable video must come back as a clean tool error, not
            # a traceback and not a crashed server.
            bad = await session.call_tool(
                "get_video_info_tool", {"url": "https://youtu.be/oooooooooop"}
            )
            print(f"bad-url call isError={bad.is_error} "
                  f"(server still alive: {not bad.is_error or True})")
            if not bad.is_error:
                print("FAIL: expected an error for an invalid video")
                return 1
            print("OK: invalid input surfaces as a clean tool error")

            if with_network:
                found = await session.call_tool(
                    "search_youtube_tool",
                    {"query": "anthropic claude code", "limit": 2},
                )
                results = (found.structured_content or {}).get("results", [])
                print(f"search -> {len(results)} results; first: "
                      f"{results[0]['title'][:60] if results else 'none'}")
                if not results:
                    print("FAIL: search returned nothing")
                    return 1

                url = results[0]["url"]
                info = await session.call_tool("get_video_info_tool", {"url": url})
                info_payload = info.structured_content or {}
                print(f"info -> {info_payload.get('title', '')[:60]} "
                      f"({info_payload.get('duration')})")

                transcript = await session.call_tool(
                    "get_transcript_tool",
                    {"url": url, "start": "0:00", "end": "0:30", "format": "text"},
                )
                tp = transcript.structured_content or {}
                text = (tp.get("text") or "")[:120].replace("\n", " ")
                print(f"transcript ({tp.get('method')}) -> {text}…")
                if not text:
                    print("FAIL: empty transcript")
                    return 1
                print("OK: network tools work end to end")

            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--network" in sys.argv)))

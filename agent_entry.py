#!/usr/bin/env python3
"""Console entry point shipped alongside the GUI as ``ytdl-agent``.

This exists so someone who downloaded the built app -- and has no Python,
no repo checkout, and no interest in either -- can still connect it to
Claude Code:

    claude mcp add youtube-downloader --scope user -- \
        "C:/.../YouTube Downloader/ytdl-agent.exe" mcp

Before this, the AI integration was reachable only by cloning the repo
and setting up a virtualenv, which meant the headline feature was
invisible and unusable for everyone who just downloaded the app.

Built as a SEPARATE console executable rather than folded into the GUI
exe on purpose: the GUI is windowed (console=False), and a windowed
process on Windows has no usable stdin/stdout -- which is exactly what an
MCP stdio server needs. One binary genuinely cannot be both.

Subcommands are the CLI's (search / info / download / transcript /
frames / config / cache / doctor / setup), plus ``mcp`` to run the MCP
server.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Frozen builds resolve imports from the bundle; a source checkout needs
# the repo root on sys.path so `python agent_entry.py` works from any cwd.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

USAGE = """YouTube Downloader -- agent tools

  ytdl-agent mcp                Run the MCP server (what Claude Code connects to)
  ytdl-agent setup              First-run setup: check everything, choose defaults
  ytdl-agent doctor             Check binaries and dependencies
  ytdl-agent config show|set    View or change settings
  ytdl-agent search "query"     Search YouTube
  ytdl-agent info URL           Video metadata
  ytdl-agent transcript URL     Timestamped transcript
  ytdl-agent frames URL         Extract frames as images
  ytdl-agent download URL       Download a video
  ytdl-agent cache show|clear   Inspect the working cache

Run any subcommand with --help for its options.

To connect this to Claude Code:
  claude mcp add youtube-downloader --scope user -- "<this executable>" mcp
"""


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        raise SystemExit(0)

    if argv[0] == "mcp":
        # Imported lazily so the CLI subcommands don't pay for the MCP
        # SDK, and so a missing/broken MCP install can't stop `doctor`
        # from running -- which is the one command you need when things
        # are broken.
        from ytdl_mcp import main as mcp_main

        mcp_main()
        return

    from ytdl_cli import main as cli_main

    cli_main(argv)


if __name__ == "__main__":
    main()

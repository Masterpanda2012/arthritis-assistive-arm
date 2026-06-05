"""Vercel ASGI entry — web console without the local robot runtime.

The full arm stack runs via ``python main.py --web`` on your machine.
This entry serves the UI + profile/help APIs for hosted preview deploys.
"""

from __future__ import annotations

from pathlib import Path

from web.server import create_app, set_robot_app

# Writable profile DB on serverless; robot orchestrator stays detached.
set_robot_app(None, Path("/tmp/assistive-arm-memory.db"))

app = create_app()

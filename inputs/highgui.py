from __future__ import annotations

import asyncio

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


_WAITKEY_LOCK = asyncio.Lock()


async def poll_key(delay_ms: int = 1) -> int:
    """Serialize HighGUI event pumping across OpenCV windows."""
    if cv2 is None:
        return -1
    async with _WAITKEY_LOCK:
        return cv2.waitKey(delay_ms) & 0xFF

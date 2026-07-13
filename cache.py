import asyncio
from pathlib import Path
from typing import Optional


class Cache:
    """Stores at most two audio files: the currently playing track and the next queued track.
    All public methods are coroutine-safe."""

    def __init__(self, cache_dir: str) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._current: Optional[Path] = None
        self._next: Optional[Path] = None
        self._lock = asyncio.Lock()
        self._sweep_orphaned_files()

    def _sweep_orphaned_files(self) -> None:
        """Delete any leftover download files from a previous run that
        didn't shut down cleanly (crash, OOM kill, power loss). Cache
        starts empty either way, so anything already in the directory
        at construction time is stale."""
        for f in self.dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass

    async def set_current(self, path: Path) -> None:
        """Set the current track, replacing any existing one (deletes old file)."""
        async with self._lock:
            if self._current and self._current.exists():
                self._current.unlink()
            self._current = path

    async def set_next(self, path: Path) -> None:
        """Set the next queued track, replacing any existing one (deletes old file)."""
        async with self._lock:
            if self._next and self._next.exists():
                self._next.unlink()
            self._next = path

    async def get_current(self) -> Optional[Path]:
        async with self._lock:
            return self._current

    async def get_next(self) -> Optional[Path]:
        async with self._lock:
            return self._next

    async def advance(self) -> Optional[Path]:
        """
        Delete current file, promote next → current, clear next.
        Returns the old current path (for logging) or None.
        """
        async with self._lock:
            old = self._current
            if old and old.exists():
                old.unlink()
            self._current = self._next
            self._next = None
            return old

    async def clear(self) -> None:
        """Delete all cached files and reset state."""
        async with self._lock:
            for f in (self._current, self._next):
                if f and f.exists():
                    f.unlink()
            self._current = None
            self._next = None
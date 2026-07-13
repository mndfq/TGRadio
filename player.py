import asyncio
import logging
import random
from pathlib import Path
from typing import List, Optional

from pyrogram.errors import FloodWait
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

from cache import Cache

logger = logging.getLogger(__name__)


class Player:
    """Manages continuous, random playback from a Telegram channel’s audio messages."""

    def __init__(
        self,
        call_client: PyTgCalls,
        chat_id: int,
        audio_ids_ref: List[int],
        cache: Cache,
        download_func,
    ) -> None:
        self.call = call_client
        self.chat_id = chat_id
        self.audio_ids_ref = audio_ids_ref          # live reference to the client’s list
        self.cache = cache
        self.download = download_func               # async (msg_id) -> Path

        self._stream_ended = asyncio.Event()
        self._skip_requested = False
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None

        # Register low‑level stream callbacks
        @self.call.on_stream_end()
        async def on_end(_, update):
            if update.chat_id == self.chat_id:
                self._stream_ended.set()

        # Registered defensively: not confirmed present on every pytgcalls
        # 2.x release. If it's missing on your installed version, stream
        # errors will just surface as exceptions around play() instead.
        try:
            @self.call.on_stream_error()
            async def on_error(_, update):
                if update.chat_id == self.chat_id:
                    logger.error(f"Stream error: {update.error}")
                    self._stream_ended.set()
        except AttributeError:
            logger.warning("on_stream_error() not available on this pytgcalls version — skipping.")

    # ------------------------------------------------------------------
    # Public API called by commands / main
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Launch the background playback loop."""
        if self._loop_task is not None:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._playback_loop())

    async def request_skip(self) -> bool:
        """Skip to a new track immediately.

        If a track is already prefetched in the 'next' slot, we use it.
        Otherwise we download one on the spot so the skip actually happens
        right away instead of silently no-opping until the loop catches up.

        Returns True if playback actually switched to a new track, False if
        the skip failed (e.g. download error) and playback is unchanged.
        """
        if not self._running:
            return False

        next_track = await self.cache.get_next()
        if next_track is None:
            try:
                next_track = await self._download_random()
                await self.cache.set_next(next_track)
            except Exception as e:
                logger.error(f"Skip failed: could not fetch a replacement track: {e}")
                return False

        # Promote next -> current (deletes old current file, clears next slot)
        # so cache state matches what's about to play. This also means a
        # second rapid skip will correctly fetch a *new* track instead of
        # replaying the one we just switched to.
        await self.cache.advance()
        await self._play_track(next_track)
        logger.info("Skip: now playing new track.")

        self._skip_requested = True
        self._stream_ended.set()   # wake the loop so it refills the next slot
        return True

    async def request_stop(self) -> None:
        """Stop playback and leave the voice chat, but keep the app alive."""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        try:
            await self.call.leave_group_call(self.chat_id)
        except Exception:
            pass
        await self.cache.clear()
        logger.info("Playback stopped by user request.")

    async def shutdown(self) -> None:
        """Full shutdown: stop playback and clean up (cache is cleared by main)."""
        await self.request_stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _download_random(self) -> Path:
        """Pick a random audio ID, download it, return the file path.
        Retries with a new ID on failure, backing off on repeated errors
        and honoring Telegram's FloodWait instead of hammering the API."""
        ids = self.audio_ids_ref
        if not ids:
            logger.error("Song list empty. Will retry in 60s.")
            raise RuntimeError("No audio messages available.")
        attempt = 0
        while True:
            msg_id = random.choice(ids)
            try:
                path = await self.download(msg_id)
                return path
            except FloodWait as e:
                logger.warning(f"FloodWait: sleeping {e.value}s before retrying download.")
                await asyncio.sleep(e.value)
            except Exception as e:
                attempt += 1
                backoff = min(60, 2 ** attempt)
                logger.error(f"Download failed for msg {msg_id}: {e}. Retrying in {backoff}s …")
                await asyncio.sleep(backoff)

    async def _fill_cache(self) -> None:
        """Ensure both cache slots are filled (called at startup and after errors)."""
        if await self.cache.get_current() is None:
            path = await self._download_random()
            await self.cache.set_current(path)
        if await self.cache.get_next() is None:
            path = await self._download_random()
            await self.cache.set_next(path)

    async def _rotate_cache(self) -> None:
        """
        Standard cycle:
        - delete old current file
        - promote next → current
        - download a new file to fill the next slot
        """
        old = await self.cache.advance()
        if old:
            logger.info(f"Finished playing {old.name}")
        # Fill the now-empty next slot
        path = await self._download_random()
        await self.cache.set_next(path)

    async def _play_track(self, path: Path) -> None:
        """Start streaming the given file in the voice chat.

        play() joins the call automatically if not already in it, so no
        separate join step is needed. Retries broadly on failure since the
        exact exception types raised when no voice chat is active yet
        aren't confirmed for every pytgcalls 2.x release — check your
        installed version's docs if this loops unexpectedly.
        """
        logger.info(f"Now playing: {path.name}")
        self._stream_ended.clear()
        stream = MediaStream(str(path), video_flags=MediaStream.Flags.IGNORE)
        while True:
            try:
                await self.call.play(self.chat_id, stream)
                return
            except Exception as e:
                logger.warning(f"Could not start stream (no active voice chat yet?): {e}. Retrying in 30s …")
                await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Main playback loop
    # ------------------------------------------------------------------
    async def _playback_loop(self) -> None:
        while self._running:
            try:
                # Pre-load cache if needed (first run / recovery)
                await self._fill_cache()

                current = await self.cache.get_current()
                if current is None:
                    logger.warning("Cache empty – re‑attempting fill …")
                    await asyncio.sleep(5)
                    continue

                # Start playing
                await self._play_track(current)

                # Wait for track end or a skip request
                await self._stream_ended.wait()

                if not self._running:
                    break

                # Check if we advanced manually (skip)
                if self._skip_requested:
                    self._skip_requested = False
                    # The skip already changed the current track and cleared the event.
                    # We just need to refill the cache if necessary.
                    if await self.cache.get_next() is None:
                        try:
                            path = await self._download_random()
                            await self.cache.set_next(path)
                        except Exception as e:
                            logger.error(f"Failed to refill next slot after skip: {e}")
                    continue

                # Normal end of track: rotate cache
                await self._rotate_cache()

            except Exception as e:
                logger.exception(f"Unexpected error in playback loop: {e}. Restarting loop in 5s.")
                await asyncio.sleep(5)
                # play() rejoins automatically on the next iteration if disconnected
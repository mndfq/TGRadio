import logging
from pathlib import Path
from typing import List, Optional, Set

from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls

from config import API_ID, API_HASH, PHONE, SESSION_NAME, CHANNEL_ID, ADMIN_ID, CACHE_DIR

logger = logging.getLogger(__name__)


class TGRadioClient:
    """Wraps the Telegram client, voice-call interface, download logic, and dynamic audio-index updates."""

    def __init__(self) -> None:
        self.app = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=PHONE,
        )
        self.call_client = PyTgCalls(self.app)
        self.chat_id: int = 0
        self.audio_message_ids: List[int] = []
        self._seen_audio_ids: Set[int] = set()  # avoids duplicates in dynamic updates
        self.shutdown_event = None  # set later from outside

    async def start(self) -> None:
        await self.app.start()
        await self.call_client.start()
        # Resolve channel
        if CHANNEL_ID.lstrip("-").isdigit():
            chat = await self.app.get_chat(int(CHANNEL_ID))
        else:
            chat = await self.app.get_chat(CHANNEL_ID)
        self.chat_id = chat.id
        logger.info(f"Connected to channel '{chat.title}' (ID: {self.chat_id})")

        # Register handler for new audio messages (dynamic list update).
        # Uses the same audio-detection rule as get_audio_messages() below
        # (audio/voice, or a document whose mime type starts with "audio/")
        # so a stray PDF or image sent to the channel doesn't get queued
        # as a track just because it arrived live instead of during the
        # initial scan.
        @self.app.on_message(filters.chat(self.chat_id) & (filters.audio | filters.voice | filters.document))
        async def new_audio_handler(_, msg: Message):
            if not self._is_audio_message(msg):
                return
            if msg.id not in self._seen_audio_ids:
                self._seen_audio_ids.add(msg.id)
                self.audio_message_ids.append(msg.id)
                logger.info(f"New audio message added (ID={msg.id}), total tracks: {len(self.audio_message_ids)}")

    @staticmethod
    def _is_audio_message(msg: Message) -> bool:
        return bool(
            msg.audio or msg.voice or (
                msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio/")
            )
        )

    async def stop(self) -> None:
        try:
            await self.call_client.leave_group_call(self.chat_id)
        except Exception:
            pass  # not in a call — fine during shutdown
        await self.call_client.stop()
        await self.app.stop()

    async def get_audio_messages(self) -> List[int]:
        """Fetch all existing audio message IDs from the channel (initial scan)."""
        ids = []
        logger.info("Scanning channel for existing audio files …")
        async for msg in self.app.get_chat_history(self.chat_id):
            if self._is_audio_message(msg) and msg.id not in self._seen_audio_ids:
                self._seen_audio_ids.add(msg.id)
                ids.append(msg.id)
        logger.info(f"Initial scan found {len(ids)} audio messages")
        return ids

    async def download_audio(self, message_id: int) -> Path:
        """Download an audio message and return its local path."""
        msg: Message = await self.app.get_messages(self.chat_id, message_id)
        if not msg:
            raise ValueError(f"Message {message_id} not found")

        # Determine file extension
        ext = ".ogg"  # default fallback
        if msg.audio:
            ext = Path(msg.audio.file_name).suffix if msg.audio.file_name else ".mp3"
        elif msg.voice:
            ext = ".ogg"
        elif msg.document:
            name = msg.document.file_name or ""
            ext = Path(name).suffix or ".unknown"

        dest = Path(CACHE_DIR) / f"download_{message_id}{ext}"
        await msg.download(file_name=str(dest))
        logger.debug(f"Downloaded message {message_id} -> {dest}")
        return dest

    def register_commands(self, player) -> None:
        """Set up admin-only commands (/skip, /stop, /shutdown)."""
        @self.app.on_message(filters.private & filters.user(ADMIN_ID) & filters.command(["skip", "stop", "shutdown"]))
        async def handler(_, msg: Message):
            cmd = msg.command[0]
            if cmd == "skip":
                ok = await player.request_skip()
                if ok:
                    await msg.reply("⏭ Skipped to next track")
                else:
                    await msg.reply("⚠️ Skip failed — couldn't fetch a replacement track, still playing current one")
            elif cmd == "stop":
                await player.request_stop()
                await msg.reply("⏹ Playback stopped (bot remains running)")
            elif cmd == "shutdown":
                await msg.reply("🛑 Shutting down …")
                if self.shutdown_event:
                    self.shutdown_event.set()
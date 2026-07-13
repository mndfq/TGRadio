import asyncio
import logging
import signal
from pathlib import Path

from telegram_client import TGRadioClient
from player import Player
from cache import Cache
from config import CACHE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("tgradio")


async def main_async() -> None:
    client = TGRadioClient()
    cache = Cache(CACHE_DIR)

    # Create shutdown event and attach it to the client
    shutdown_event = asyncio.Event()
    client.shutdown_event = shutdown_event

    # Handle system signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    await client.start()
    player = None
    try:
        # Get initial audio list
        audio_ids = await client.get_audio_messages()
        if not audio_ids:
            logger.error("No audio files found in the channel. Exiting.")
            return
        client.audio_message_ids = audio_ids  # initial list, will grow dynamically

        player = Player(
            call_client=client.call_client,
            chat_id=client.chat_id,
            audio_ids_ref=client.audio_message_ids,
            cache=cache,
            download_func=client.download_audio,
        )

        # Register admin commands
        client.register_commands(player)

        # Start playback
        await player.start()
        logger.info("TGRadio v1.0 is running. Press Ctrl+C to stop.")

        # Wait until shutdown is requested (command or signal)
        await shutdown_event.wait()
        logger.info("Shutting down …")
    finally:
        # Runs on clean shutdown AND on any exception, so a mid-run crash
        # doesn't leak a joined voice chat or leftover cache files into
        # the next restart attempt.
        if player is not None:
            await player.shutdown()
        await cache.clear()
        await client.stop()
        logger.info("Shutdown complete. Goodbye.")


def main() -> None:
    """Entry point with top‑level restart on catastrophic failures."""
    while True:
        try:
            asyncio.run(main_async())
            break
        except Exception as e:
            logger.exception(f"Fatal error in main loop: {e}. Restarting in 10 seconds …")
            asyncio.run(asyncio.sleep(10))


if __name__ == "__main__":
    main()
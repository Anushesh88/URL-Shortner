import asyncio
import logging
import signal
import sys
import os

# Add parent directory to sys.path so app imports resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.click_processor import click_processor
from app.models.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ClickWorker] %(message)s",
)
logger = logging.getLogger("click_worker")

shutdown_event = asyncio.Event()


def handle_stop_signals():
    logger.info("Received termination signal. Shutting down click worker...")
    shutdown_event.set()


async def main():
    logger.info("Starting Click Analytics Batch Worker...")
    await init_db()

    # Handle graceful exit
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_stop_signals)
        except NotImplementedError:
            # Signal handling on Windows
            pass

    flush_interval = settings.CLICK_FLUSH_INTERVAL
    batch_size = settings.CLICK_BATCH_SIZE

    while not shutdown_event.is_set():
        try:
            processed = await click_processor.process_batch(batch_size=batch_size)
            if processed == 0:
                # Idle wait
                await asyncio.sleep(flush_interval)
        except Exception as e:
            logger.error(f"Error in batch processing loop: {e}", exc_info=True)
            await asyncio.sleep(flush_interval)

    logger.info("Click worker stopped gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped by user.")

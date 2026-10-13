"""
Cusp Worker - Service Entrypoint.
Starts the background consumer group worker to process Redis Streams events.
"""

from __future__ import annotations

import asyncio
import logging
import signal

try:
    from .config import get_worker_settings
    from .consumer import WorkerConsumer
except ImportError:
    try:
        from app.config import get_worker_settings
        from app.consumer import WorkerConsumer
    except ImportError:
        from services.worker.app.config import get_worker_settings
        from services.worker.app.consumer import WorkerConsumer


def setup_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )


async def async_main() -> None:
    settings = get_worker_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("cusp.worker")

    logger.info("Initializing Cusp event consumer...")
    consumer = WorkerConsumer(settings=settings)

    # Set up signal handlers for graceful shutdown (Unix and Windows compatible)
    loop = asyncio.get_running_loop()

    def _shutdown():
        logger.info("Shutdown signal received, stopping consumer...")
        consumer.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Signal handlers not implemented on Windows event loop for non-main thread
            signal.signal(sig, lambda *_: _shutdown())

    await consumer.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()

import signal
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name('.env'))

from app import create_app
from app.config import Config
from app.services.Feed import RssFeedRefreshManager
from app.services.ServerDataService import ServerStatusManager


def main() -> None:
    app = create_app()
    app.logger.info('starting background worker process')

    server_status_manager = ServerStatusManager(app)

    rss_feed_refresh_manager = None
    if Config.RSS_REFRESH_ENABLED:
        rss_feed_refresh_manager = RssFeedRefreshManager(
            app,
            interval=Config.RSS_REFRESH_INTERVAL,
        )

    stop_requested = {'value': False}

    def handle_signal(signum, frame):
        app.logger.info('worker received signal %s, shutting down', signum)
        stop_requested['value'] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        while not stop_requested['value']:
            time.sleep(1)
    finally:
        server_status_manager.stop()
        if rss_feed_refresh_manager is not None:
            rss_feed_refresh_manager.stop()
        app.logger.info('background worker process stopped')


if __name__ == '__main__':
    main()
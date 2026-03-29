from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name('.env'))

from app import create_app
from app.config import Config

if __name__ == '__main__':
    app = create_app()

    debug = Config.DEBUG
    host = Config.HOST
    port = Config.PORT

    app.run(debug=debug, host=host, port=port)

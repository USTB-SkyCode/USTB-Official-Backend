from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name('.env'))

from app import create_app


app = create_app()
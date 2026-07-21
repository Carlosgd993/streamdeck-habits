import os

from dotenv import load_dotenv

from config import ENV_FILE


def get_token():
    load_dotenv(ENV_FILE)
    return os.environ.get("TICKTICK_ACCESS_TOKEN")

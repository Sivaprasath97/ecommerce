import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")
    MONGO_URI = os.getenv("MONGO_URI", "")
    MONGO_DB = os.getenv("MONGO_DB", "ecommerce")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@shop.com")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    # Vercel caps request bodies at 4.5 MB, so keep uploads under that.
    MAX_CONTENT_LENGTH = int(4.4 * 1024 * 1024)
    MAX_IMAGE_BYTES = 4 * 1024 * 1024

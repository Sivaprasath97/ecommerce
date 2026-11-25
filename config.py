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
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB uploads
    ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

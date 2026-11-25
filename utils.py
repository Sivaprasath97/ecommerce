import math
import re
import secrets
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from bson import Binary, ObjectId
from bson.errors import InvalidId
from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.utils import secure_filename


def oid(value):
    """Convert a string to ObjectId or 404."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        abort(404)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or secrets.token_hex(4)


def unique_slug(collection, text, exclude_id=None):
    base = slugify(text)
    slug, n = base, 2
    while True:
        query = {"slug": slug}
        if exclude_id:
            query["_id"] = {"$ne": exclude_id}
        if not collection.find_one(query, {"_id": 1}):
            return slug
        slug = f"{base}-{n}"
        n += 1


def to_float(value, default=0.0):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.user:
            flash("Please log in to continue.", "warning")
            if request.method == "GET":
                nxt = request.full_path
            else:  # a POST can't be replayed after login; return to the page it came from
                ref = urlparse(request.referrer or "")
                nxt = (ref.path + ("?" + ref.query if ref.query else "")) or "/"
            return redirect(url_for("auth.login", next=nxt))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not g.admin:
            flash("Admin login required.", "warning")
            return redirect(url_for("admin.login", next=request.full_path))
        return f(*args, **kwargs)
    return wrapper


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf")
        if not token or token != session.get("_csrf"):
            abort(400, "Invalid or missing form token. Please reload the page and try again.")


def safe_next(target, fallback):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "gif": "image/gif", "webp": "image/webp"}


def save_image(file_storage):
    """Store an uploaded image in MongoDB and return its URL, or None.

    Images live in the database (not on disk) so uploads work on serverless
    hosts like Vercel, whose filesystem is read-only.
    """
    from db import mongo  # local import avoids a circular import at startup

    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in IMAGE_MIME:
        flash("Unsupported image type. Use png, jpg, jpeg, gif or webp.", "danger")
        return None
    data = file_storage.read()
    if len(data) > current_app.config["MAX_IMAGE_BYTES"]:
        flash("Image is too large (max 4 MB).", "danger")
        return None
    res = mongo.db.images.insert_one({
        "data": Binary(data), "content_type": IMAGE_MIME[ext],
        "filename": secure_filename(file_storage.filename), "size": len(data),
        "created_at": datetime.utcnow(),
    })
    return url_for("shop.image", image_id=str(res.inserted_id))


class Pagination:
    def __init__(self, page, per_page, total):
        self.per_page = max(per_page, 1)
        self.total = total
        self.pages = max(math.ceil(total / self.per_page), 1)
        self.page = min(max(page, 1), self.pages)
        self.skip = (self.page - 1) * self.per_page
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages


def discount_pct(product):
    mrp, price = product.get("mrp") or 0, product.get("price") or 0
    if mrp and mrp > price:
        return int(round((mrp - price) / mrp * 100))
    return 0


# MongoDB expression computing a product's discount percent (for filters / sorting)
DISCOUNT_EXPR = {"$cond": [
    {"$gt": [{"$ifNull": ["$mrp", 0]}, "$price"]},
    {"$multiply": [{"$divide": [{"$subtract": ["$mrp", "$price"]}, "$mrp"]}, 100]},
    0,
]}


def compute_totals(subtotal, settings):
    subtotal = round(subtotal, 2)
    free_over = to_float(settings.get("free_shipping_over"))
    if subtotal == 0 or (free_over > 0 and subtotal >= free_over):
        shipping = 0.0
    else:
        shipping = to_float(settings.get("shipping_fee"))
    tax = round(subtotal * to_float(settings.get("tax_rate")) / 100, 2)
    return {"subtotal": subtotal, "shipping": shipping, "tax": tax,
            "total": round(subtotal + shipping + tax, 2)}

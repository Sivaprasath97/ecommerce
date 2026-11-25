"""MongoDB Atlas connection, indexes, site settings and first-run seed data."""
from datetime import datetime

from pymongo import ASCENDING, DESCENDING, MongoClient
from werkzeug.security import generate_password_hash


class Mongo:
    client = None
    db = None


mongo = Mongo()

ORDER_STATUSES = ["pending", "processing", "shipped", "delivered", "cancelled"]

# Every key here is editable from Admin -> Settings and is rendered on the store.
DEFAULT_SETTINGS = {
    "store_name": "ShopEase",
    "tagline": "Quality products, delivered fast",
    "announcement": "Free shipping on orders over 100!",
    "banner_title": "Discover Something New",
    "banner_subtitle": "Browse our latest collection hand-picked for you.",
    "banner_image": "https://picsum.photos/seed/banner/1600/500",
    "primary_color": "#4f46e5",
    "accent_color": "#f59e0b",
    "currency_symbol": "$",
    "shipping_fee": 5.0,
    "free_shipping_over": 100.0,
    "tax_rate": 5.0,
    "products_per_page": 12,
    "allow_registration": True,
    "contact_email": "support@shopease.com",
    "footer_text": "© ShopEase. All rights reserved.",
}


def init_db(app):
    uri = app.config["MONGO_URI"]
    if not uri or "<cluster>" in uri:
        raise RuntimeError(
            "MONGO_URI is not configured. Open .env and replace <cluster> with "
            "your MongoDB Atlas cluster host (e.g. cluster0.abcde.mongodb.net)."
        )
    mongo.client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    mongo.client.admin.command("ping")
    mongo.db = mongo.client[app.config["MONGO_DB"]]
    _ensure_indexes()
    _seed(app)
    _migrate()


def _ensure_indexes():
    db = mongo.db
    db.users.create_index("email", unique=True)
    db.categories.create_index("slug", unique=True)
    db.products.create_index("slug", unique=True)
    db.products.create_index([("category_id", ASCENDING), ("active", ASCENDING)])
    db.products.create_index([("name", "text"), ("description", "text")])
    db.carts.create_index("user_id", unique=True)
    db.orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    db.orders.create_index("order_number", unique=True)
    db.banners.create_index([("position", ASCENDING)])


CATEGORY_ICONS = {
    "Electronics": "laptop", "Fashion": "handbag", "Home & Kitchen": "house-heart",
    "Books": "book", "Beauty": "stars", "Sports": "bicycle", "Toys": "puzzle",
    "Grocery": "basket", "Mobiles": "phone",
}

SAMPLE_MRP = {
    "wireless-headphones": 129.99, "smart-watch": 199.00, "portable-speaker": 69.00,
    "classic-denim-jacket": 99.90, "running-sneakers": 129.00, "ceramic-mug-set": 34.99,
    "chef-knife": 59.95, "the-pragmatic-programmer": 44.00,
}


def _migrate():
    """Bring data created by older versions up to the current schema."""
    db = mongo.db
    for c in db.categories.find({"icon": {"$exists": False}}):
        db.categories.update_one({"_id": c["_id"]},
                                 {"$set": {"icon": CATEGORY_ICONS.get(c["name"], "grid")}})
    for slug, mrp in SAMPLE_MRP.items():
        db.products.update_one({"slug": slug, "mrp": {"$exists": False}}, {"$set": {"mrp": mrp}})
    db.products.update_many({"mrp": {"$exists": False}}, {"$set": {"mrp": None}})
    if db.banners.count_documents({}) == 0:
        now = datetime.utcnow()
        db.banners.insert_many([
            {"title": "Big Savings Week", "subtitle": "Top electronics at their lowest prices",
             "image_url": "https://picsum.photos/seed/slide-tech/1600/500",
             "link": "/products?category=electronics", "button_text": "Shop electronics",
             "position": 1, "active": True, "created_at": now},
            {"title": "New Season Styles", "subtitle": "Fresh fashion picks just landed",
             "image_url": "https://picsum.photos/seed/slide-fashion/1600/500",
             "link": "/products?category=fashion", "button_text": "Explore fashion",
             "position": 2, "active": True, "created_at": now},
            {"title": "Make Home Better", "subtitle": "Kitchen & home essentials from great brands",
             "image_url": "https://picsum.photos/seed/slide-home/1600/500",
             "link": "/products?category=home-kitchen", "button_text": "Shop home",
             "position": 3, "active": True, "created_at": now},
        ])


def get_settings():
    doc = mongo.db.settings.find_one({"_id": "site"}) or {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in doc.items() if k != "_id"})
    return merged


def _seed(app):
    db = mongo.db
    now = datetime.utcnow()

    if not db.settings.find_one({"_id": "site"}):
        db.settings.insert_one({"_id": "site", **DEFAULT_SETTINGS})

    if not db.users.find_one({"role": "admin"}):
        db.users.update_one(
            {"email": app.config["ADMIN_EMAIL"].lower()},
            {"$set": {
                "name": "Administrator",
                "password": generate_password_hash(app.config["ADMIN_PASSWORD"]),
                "role": "admin",
                "active": True,
            }, "$setOnInsert": {"created_at": now, "phone": "", "address": {}}},
            upsert=True,
        )

    if db.categories.count_documents({}) == 0:
        cats = [
            ("Electronics", "Gadgets and devices"),
            ("Fashion", "Clothing and accessories"),
            ("Home & Kitchen", "Everything for your home"),
            ("Books", "Read something great"),
        ]
        db.categories.insert_many([
            {"name": n, "slug": n.lower().replace(" & ", "-").replace(" ", "-"),
             "description": d, "active": True, "created_at": now}
            for n, d in cats
        ])

    if db.products.count_documents({}) == 0:
        cat_ids = {c["name"]: c["_id"] for c in db.categories.find()}
        samples = [
            ("Wireless Headphones", "Electronics", 79.99, 25, True,
             "Over-ear Bluetooth headphones with 30-hour battery life."),
            ("Smart Watch", "Electronics", 129.00, 15, True,
             "Track fitness, heart rate and notifications on your wrist."),
            ("Portable Speaker", "Electronics", 45.50, 40, False,
             "Waterproof speaker with deep bass."),
            ("Classic Denim Jacket", "Fashion", 59.90, 30, True,
             "Timeless denim jacket in a relaxed fit."),
            ("Running Sneakers", "Fashion", 89.00, 20, False,
             "Lightweight sneakers built for comfort."),
            ("Ceramic Mug Set", "Home & Kitchen", 24.99, 50, False,
             "Set of 4 hand-glazed ceramic mugs."),
            ("Chef Knife", "Home & Kitchen", 39.95, 18, True,
             "8-inch stainless steel chef knife."),
            ("The Pragmatic Programmer", "Books", 34.00, 60, False,
             "A classic guide to software craftsmanship."),
        ]
        docs = []
        for name, cat, price, stock, featured, desc in samples:
            slug = name.lower().replace(" ", "-")
            docs.append({
                "name": name, "slug": slug, "description": desc,
                "price": price, "stock": stock,
                "category_id": cat_ids.get(cat),
                "image_url": f"https://picsum.photos/seed/{slug}/600/450",
                "featured": featured, "active": True,
                "created_at": now, "updated_at": now,
            })
        db.products.insert_many(docs)

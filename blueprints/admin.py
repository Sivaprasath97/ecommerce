"""Separate admin panel (/admin) with its own login session."""
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash

from db import DEFAULT_SETTINGS, ORDER_STATUSES, mongo
from utils import (Pagination, admin_required, oid, safe_next, save_image, to_float, to_int,
                   unique_slug)

bp = Blueprint("admin", __name__)


# -------------------------------------------------------------------- auth

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.admin:
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = mongo.db.users.find_one({"email": email, "role": "admin"})
        if (not user or not user.get("active", True)
                or not check_password_hash(user["password"], request.form.get("password", ""))):
            flash("Invalid admin credentials.", "danger")
        else:
            session["admin_id"] = str(user["_id"])
            flash(f"Welcome, {user['name']}.", "success")
            return redirect(safe_next(request.args.get("next"), url_for("admin.dashboard")))
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("admin_id", None)
    flash("Logged out of admin panel.", "info")
    return redirect(url_for("admin.login"))


# --------------------------------------------------------------- dashboard

@bp.route("/")
@admin_required
def dashboard():
    db = mongo.db
    revenue = next(iter(db.orders.aggregate([
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}])), {}).get("total", 0)
    status_counts = {s: 0 for s in ORDER_STATUSES}
    for r in db.orders.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        status_counts[r["_id"]] = r["n"]

    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=13)
    daily = {r["_id"]: r for r in db.orders.aggregate([
        {"$match": {"created_at": {"$gte": since}, "status": {"$ne": "cancelled"}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "sales": {"$sum": "$total"}, "orders": {"$sum": 1}}}])}
    chart = {"labels": [], "sales": [], "orders": []}
    for i in range(14):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        chart["labels"].append(d[5:])
        chart["sales"].append(round(daily.get(d, {}).get("sales", 0), 2))
        chart["orders"].append(daily.get(d, {}).get("orders", 0))

    stats = {
        "revenue": revenue,
        "orders": sum(status_counts.values()),
        "customers": db.users.count_documents({"role": "customer"}),
        "products": db.products.count_documents({}),
    }
    recent = list(db.orders.find().sort("created_at", -1).limit(8))
    low_stock = list(db.products.find({"stock": {"$lte": 5}}).sort("stock", 1).limit(8))
    return render_template("admin/dashboard.html", stats=stats, status_counts=status_counts,
                           recent=recent, low_stock=low_stock, chart=chart)


# ---------------------------------------------------------------- products

@bp.route("/products")
@admin_required
def products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    query = {}
    if q:
        query["name"] = re.compile(re.escape(q), re.IGNORECASE)
    if cat:
        query["category_id"] = oid(cat)
    pager = Pagination(to_int(request.args.get("page"), 1), 15,
                       mongo.db.products.count_documents(query))
    items = list(mongo.db.products.find(query).sort("created_at", -1)
                 .skip(pager.skip).limit(pager.per_page))
    categories = list(mongo.db.categories.find().sort("name", 1))
    cat_names = {c["_id"]: c["name"] for c in categories}
    return render_template("admin/products.html", products=items, pager=pager, q=q,
                           categories=categories, cat_names=cat_names, cat=cat)


def _product_from_form(existing=None):
    f = request.form
    data = {
        "name": f.get("name", "").strip(),
        "description": f.get("description", "").strip(),
        "price": to_float(f.get("price"), -1),
        "mrp": to_float(f.get("mrp"), 0) or None,
        "stock": to_int(f.get("stock"), -1),
        "category_id": oid(f["category_id"]) if f.get("category_id") else None,
        "image_url": f.get("image_url", "").strip(),
        "featured": bool(f.get("featured")),
        "active": bool(f.get("active")),
        "updated_at": datetime.utcnow(),
    }
    uploaded = save_image(request.files.get("image_file"))
    if uploaded:
        data["image_url"] = uploaded
    errors = []
    if len(data["name"]) < 2:
        errors.append("Product name is required.")
    if data["price"] < 0:
        errors.append("Price must be a positive number.")
    if data["stock"] < 0:
        errors.append("Stock must be zero or more.")
    if data["mrp"] is not None and data["mrp"] < data["price"]:
        errors.append("MRP must be greater than or equal to the selling price (or left empty).")
    return data, errors


@bp.route("/products/new", methods=["GET", "POST"])
@admin_required
def product_new():
    categories = list(mongo.db.categories.find().sort("name", 1))
    if request.method == "POST":
        data, errors = _product_from_form()
        if not errors:
            data["slug"] = unique_slug(mongo.db.products, data["name"])
            data["created_at"] = datetime.utcnow()
            mongo.db.products.insert_one(data)
            flash("Product created.", "success")
            return redirect(url_for("admin.products"))
        for e in errors:
            flash(e, "danger")
        return render_template("admin/product_form.html", product=data, categories=categories)
    return render_template("admin/product_form.html",
                           product={"active": True, "featured": False}, categories=categories)


@bp.route("/products/<pid>/edit", methods=["GET", "POST"])
@admin_required
def product_edit(pid):
    product = mongo.db.products.find_one({"_id": oid(pid)}) or abort(404)
    categories = list(mongo.db.categories.find().sort("name", 1))
    if request.method == "POST":
        data, errors = _product_from_form(product)
        if not errors:
            if data["name"] != product["name"]:
                data["slug"] = unique_slug(mongo.db.products, data["name"], product["_id"])
            mongo.db.products.update_one({"_id": product["_id"]}, {"$set": data})
            flash("Product updated.", "success")
            return redirect(url_for("admin.products"))
        for e in errors:
            flash(e, "danger")
        product.update(data)
    return render_template("admin/product_form.html", product=product, categories=categories)


@bp.route("/products/<pid>/toggle", methods=["POST"])
@admin_required
def product_toggle(pid):
    field = request.form.get("field")
    if field not in ("active", "featured"):
        abort(400)
    p = mongo.db.products.find_one({"_id": oid(pid)}) or abort(404)
    mongo.db.products.update_one({"_id": p["_id"]}, {"$set": {field: not p.get(field, False)}})
    return redirect(request.referrer or url_for("admin.products"))


@bp.route("/products/<pid>/delete", methods=["POST"])
@admin_required
def product_delete(pid):
    pid = oid(pid)
    mongo.db.products.delete_one({"_id": pid})
    mongo.db.carts.update_many({}, {"$pull": {"items": {"product_id": pid}}})
    flash("Product deleted.", "info")
    return redirect(url_for("admin.products"))


# -------------------------------------------------------------- categories

CATEGORY_ICON_CHOICES = [
    "grid", "laptop", "phone", "tv", "headphones", "camera", "smartwatch", "handbag", "bag",
    "gem", "stars", "house-heart", "lamp", "cup-hot", "basket", "book", "bicycle", "trophy",
    "puzzle", "balloon", "heart-pulse", "capsule", "car-front", "tools", "flower1", "gift",
]


def _icon(value):
    return value if value in CATEGORY_ICON_CHOICES else "grid"


@bp.context_processor
def _admin_ctx():
    return {"icon_choices": CATEGORY_ICON_CHOICES}


@bp.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if len(name) < 2:
            flash("Category name is required.", "danger")
        else:
            mongo.db.categories.insert_one({
                "name": name, "slug": unique_slug(mongo.db.categories, name),
                "description": request.form.get("description", "").strip(),
                "icon": _icon(request.form.get("icon")),
                "active": True, "created_at": datetime.utcnow()})
            flash("Category added.", "success")
        return redirect(url_for("admin.categories"))
    cats = list(mongo.db.categories.find().sort("name", 1))
    counts = {r["_id"]: r["n"] for r in mongo.db.products.aggregate(
        [{"$group": {"_id": "$category_id", "n": {"$sum": 1}}}])}
    return render_template("admin/categories.html", categories=cats, counts=counts)


@bp.route("/categories/<cid>/edit", methods=["GET", "POST"])
@admin_required
def category_edit(cid):
    cat = mongo.db.categories.find_one({"_id": oid(cid)}) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if len(name) < 2:
            flash("Category name is required.", "danger")
        else:
            update = {"name": name, "description": request.form.get("description", "").strip(),
                      "icon": _icon(request.form.get("icon")),
                      "active": bool(request.form.get("active"))}
            if name != cat["name"]:
                update["slug"] = unique_slug(mongo.db.categories, name, cat["_id"])
            mongo.db.categories.update_one({"_id": cat["_id"]}, {"$set": update})
            flash("Category updated.", "success")
            return redirect(url_for("admin.categories"))
    return render_template("admin/category_form.html", category=cat)


@bp.route("/categories/<cid>/delete", methods=["POST"])
@admin_required
def category_delete(cid):
    cid = oid(cid)
    if mongo.db.products.count_documents({"category_id": cid}):
        flash("Move or delete this category's products first.", "warning")
    else:
        mongo.db.categories.delete_one({"_id": cid})
        flash("Category deleted.", "info")
    return redirect(url_for("admin.categories"))


# ------------------------------------------------------------------ orders

@bp.route("/orders")
@admin_required
def orders():
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    query = {}
    if status:
        query["status"] = status
    if q:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [{"order_number": rx}, {"user_email": rx}, {"user_name": rx}]
    pager = Pagination(to_int(request.args.get("page"), 1), 15,
                       mongo.db.orders.count_documents(query))
    items = list(mongo.db.orders.find(query).sort("created_at", -1)
                 .skip(pager.skip).limit(pager.per_page))
    return render_template("admin/orders.html", orders=items, pager=pager, status=status, q=q)


@bp.route("/orders/<order_id>", methods=["GET", "POST"])
@admin_required
def order_detail(order_id):
    order = mongo.db.orders.find_one({"_id": oid(order_id)}) or abort(404)
    if request.method == "POST":
        new_status = request.form.get("status")
        if new_status not in ORDER_STATUSES:
            abort(400)
        if order["status"] == "cancelled":
            flash("Cancelled orders cannot be changed.", "warning")
        elif new_status != order["status"]:
            now = datetime.utcnow()
            mongo.db.orders.update_one({"_id": order["_id"]}, {
                "$set": {"status": new_status, "updated_at": now},
                "$push": {"status_history": {"status": new_status, "at": now,
                                             "by": g.admin["name"],
                                             "note": request.form.get("note", "").strip()}}})
            if new_status == "cancelled":
                for it in order["items"]:
                    mongo.db.products.update_one({"_id": it["product_id"]},
                                                 {"$inc": {"stock": it["qty"]}})
            flash(f"Order status changed to {new_status}.", "success")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    return render_template("admin/order_detail.html", order=order)


# ------------------------------------------------------------------- users

@bp.route("/users")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    query = {}
    if q:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        query["$or"] = [{"name": rx}, {"email": rx}]
    if role:
        query["role"] = role
    pager = Pagination(to_int(request.args.get("page"), 1), 20,
                       mongo.db.users.count_documents(query))
    items = list(mongo.db.users.find(query, {"password": 0}).sort("created_at", -1)
                 .skip(pager.skip).limit(pager.per_page))
    order_counts = {r["_id"]: r for r in mongo.db.orders.aggregate([
        {"$match": {"user_id": {"$in": [u["_id"] for u in items]}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}, "spent": {"$sum": "$total"}}}])}
    return render_template("admin/users.html", users=items, pager=pager, q=q, role=role,
                           order_counts=order_counts)


@bp.route("/users/<uid>/toggle", methods=["POST"])
@admin_required
def user_toggle(uid):
    user = mongo.db.users.find_one({"_id": oid(uid)}) or abort(404)
    action = request.form.get("action")
    if user["_id"] == g.admin["_id"]:
        flash("You cannot change your own account here.", "warning")
    elif action == "active":
        mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"active": not user.get("active", True)}})
        flash("User status updated.", "success")
    elif action == "role":
        new_role = "customer" if user.get("role") == "admin" else "admin"
        mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"role": new_role}})
        flash(f"{user['name']} is now {new_role}.", "success")
    return redirect(request.referrer or url_for("admin.users"))


# ----------------------------------------------------------------- banners

def _banner_from_form():
    f = request.form
    data = {
        "title": f.get("title", "").strip(),
        "subtitle": f.get("subtitle", "").strip(),
        "image_url": f.get("image_url", "").strip(),
        "link": f.get("link", "").strip() or "/products",
        "button_text": f.get("button_text", "").strip(),
        "position": to_int(f.get("position"), 1),
        "active": bool(f.get("active")),
    }
    uploaded = save_image(request.files.get("image_file"))
    if uploaded:
        data["image_url"] = uploaded
    if not (data["link"].startswith("/") or data["link"].startswith("http")):
        data["link"] = "/" + data["link"]
    return data


@bp.route("/banners", methods=["GET", "POST"])
@admin_required
def banners():
    if request.method == "POST":
        data = _banner_from_form()
        if not data["image_url"]:
            flash("A banner image (URL or upload) is required.", "danger")
        else:
            data["created_at"] = datetime.utcnow()
            mongo.db.banners.insert_one(data)
            flash("Banner added. It is live on the home page slider.", "success")
        return redirect(url_for("admin.banners"))
    items = list(mongo.db.banners.find().sort("position", 1))
    return render_template("admin/banners.html", banners=items)


@bp.route("/banners/<bid>/edit", methods=["GET", "POST"])
@admin_required
def banner_edit(bid):
    banner = mongo.db.banners.find_one({"_id": oid(bid)}) or abort(404)
    if request.method == "POST":
        data = _banner_from_form()
        if not data["image_url"]:
            flash("A banner image (URL or upload) is required.", "danger")
        else:
            mongo.db.banners.update_one({"_id": banner["_id"]}, {"$set": data})
            flash("Banner updated.", "success")
            return redirect(url_for("admin.banners"))
    return render_template("admin/banner_form.html", banner=banner)


@bp.route("/banners/<bid>/toggle", methods=["POST"])
@admin_required
def banner_toggle(bid):
    b = mongo.db.banners.find_one({"_id": oid(bid)}) or abort(404)
    mongo.db.banners.update_one({"_id": b["_id"]}, {"$set": {"active": not b.get("active")}})
    return redirect(url_for("admin.banners"))


@bp.route("/banners/<bid>/delete", methods=["POST"])
@admin_required
def banner_delete(bid):
    mongo.db.banners.delete_one({"_id": oid(bid)})
    flash("Banner deleted.", "info")
    return redirect(url_for("admin.banners"))


# ---------------------------------------------------------------- settings

NUMERIC_SETTINGS = {"shipping_fee", "free_shipping_over", "tax_rate"}
INT_SETTINGS = {"products_per_page"}
BOOL_SETTINGS = {"allow_registration"}


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        update = {}
        for key, default in DEFAULT_SETTINGS.items():
            if key in BOOL_SETTINGS:
                update[key] = bool(request.form.get(key))
            elif key in NUMERIC_SETTINGS:
                update[key] = max(to_float(request.form.get(key), default), 0)
            elif key in INT_SETTINGS:
                update[key] = min(max(to_int(request.form.get(key), default), 1), 100)
            else:
                update[key] = request.form.get(key, "").strip()
        uploaded = save_image(request.files.get("banner_file"))
        if uploaded:
            update["banner_image"] = uploaded
        for color in ("primary_color", "accent_color"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", update[color]):
                update[color] = DEFAULT_SETTINGS[color]
        mongo.db.settings.update_one({"_id": "site"}, {"$set": update}, upsert=True)
        flash("Store settings saved. Changes are live on the storefront.", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html")

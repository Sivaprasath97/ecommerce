import re
import secrets
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import mongo
from utils import (DISCOUNT_EXPR, Pagination, compute_totals, login_required, oid, to_float,
                   to_int)

bp = Blueprint("shop", __name__)

SORTS = {
    "newest": {"created_at": -1, "_id": -1},
    "price_asc": {"price": 1, "_id": 1},
    "price_desc": {"price": -1, "_id": 1},
    "discount": {"discount": -1, "_id": 1},
    "name": {"name": 1, "_id": 1},
}
SORT_LABELS = [("newest", "Newest First"), ("price_asc", "Price: Low to High"),
               ("price_desc", "Price: High to Low"), ("discount", "Discount"), ("name", "Name")]
DISCOUNT_STEPS = [10, 20, 30, 40, 50]


def _visible_query():
    active_cat_ids = [c["_id"] for c in mongo.db.categories.find({"active": True}, {"_id": 1})]
    return {"active": True, "$or": [{"category_id": {"$in": active_cat_ids}},
                                    {"category_id": None}]}


# ---------------------------------------------------------------- products

@bp.route("/")
def home():
    db = mongo.db
    base = _visible_query()
    banners = list(db.banners.find({"active": True}).sort("position", 1))
    deals = list(db.products.aggregate([
        {"$match": {**base, "stock": {"$gt": 0}}},
        {"$addFields": {"discount": DISCOUNT_EXPR}},
        {"$match": {"discount": {"$gt": 0}}},
        {"$sort": {"discount": -1}}, {"$limit": 10}]))
    featured = list(db.products.find({**base, "featured": True}).sort("created_at", -1).limit(10))
    latest = list(db.products.find(base).sort("created_at", -1).limit(10))
    categories = list(db.categories.find({"active": True}).sort("name", 1))
    rows = []
    for c in categories:
        items = list(db.products.find({"active": True, "category_id": c["_id"]})
                     .sort("created_at", -1).limit(10))
        if items:
            rows.append((c, items))
    return render_template("shop/home.html", banners=banners, deals=deals, featured=featured,
                           latest=latest, categories=categories, rows=rows)


@bp.route("/products")
def products():
    args = request.args
    q = args.get("q", "").strip()
    cat_slug = args.get("category", "")
    sort = args.get("sort", "newest") if args.get("sort") in SORTS else "newest"
    page = to_int(args.get("page"), 1)
    min_price = to_float(args.get("min"), 0)
    max_price = to_float(args.get("max"), 0)
    min_discount = to_int(args.get("discount"), 0)
    in_stock = args.get("in_stock") == "1"

    query = _visible_query()
    category = None
    if cat_slug:
        category = mongo.db.categories.find_one({"slug": cat_slug, "active": True})
        if not category:
            abort(404)
        query = {"active": True, "category_id": category["_id"]}
    and_clauses = []
    if q:
        rx = re.compile(re.escape(q), re.IGNORECASE)
        and_clauses.append({"$or": [{"name": rx}, {"description": rx}]})
    price = {}
    if min_price > 0:
        price["$gte"] = min_price
    if max_price > 0:
        price["$lte"] = max_price
    if price:
        query["price"] = price
    if in_stock:
        query["stock"] = {"$gt": 0}
    if min_discount:
        and_clauses.append({"$expr": {"$gte": [DISCOUNT_EXPR, min_discount]}})
    if and_clauses:
        query["$and"] = and_clauses

    total = mongo.db.products.count_documents(query)
    pager = Pagination(page, to_int(g.settings.get("products_per_page"), 12), total)
    items = list(mongo.db.products.aggregate([
        {"$match": query}, {"$addFields": {"discount": DISCOUNT_EXPR}},
        {"$sort": SORTS[sort]}, {"$skip": pager.skip}, {"$limit": pager.per_page}]))
    return render_template("shop/products.html", products=items, pager=pager, q=q,
                           category=category, sort=sort, sort_labels=SORT_LABELS,
                           discount_steps=DISCOUNT_STEPS, min_price=min_price,
                           max_price=max_price, min_discount=min_discount, in_stock=in_stock)


@bp.route("/product/<slug>")
def product_detail(slug):
    product = mongo.db.products.find_one({"slug": slug, "active": True})
    if not product:
        abort(404)
    category = mongo.db.categories.find_one({"_id": product.get("category_id")})
    related = list(mongo.db.products.find({
        "active": True, "category_id": product.get("category_id"),
        "_id": {"$ne": product["_id"]}}).limit(4))
    return render_template("shop/product_detail.html", product=product,
                           category=category, related=related)


# -------------------------------------------------------------------- cart

def cart_details(user_id):
    cart = mongo.db.carts.find_one({"user_id": user_id}) or {"items": []}
    ids = [i["product_id"] for i in cart["items"]]
    products = {p["_id"]: p for p in mongo.db.products.find({"_id": {"$in": ids}, "active": True})}
    lines, subtotal, mrp_total = [], 0.0, 0.0
    for item in cart["items"]:
        p = products.get(item["product_id"])
        if not p:
            continue  # product removed/disabled by admin
        line_total = round(p["price"] * item["qty"], 2)
        lines.append({"product": p, "qty": item["qty"], "subtotal": line_total,
                      "available": p["stock"] >= item["qty"]})
        subtotal += line_total
        mrp_total += max(p.get("mrp") or 0, p["price"]) * item["qty"]
    totals = compute_totals(subtotal, g.settings)
    totals["mrp_total"] = round(mrp_total, 2)
    totals["savings"] = round(mrp_total - subtotal, 2)
    return lines, totals


def _save_cart(items):
    mongo.db.carts.update_one(
        {"user_id": g.user["_id"]},
        {"$set": {"items": items, "updated_at": datetime.utcnow()}},
        upsert=True)


def _cart_items():
    cart = mongo.db.carts.find_one({"user_id": g.user["_id"]})
    return cart["items"] if cart else []


@bp.route("/cart")
@login_required
def cart():
    lines, totals = cart_details(g.user["_id"])
    return render_template("shop/cart.html", lines=lines, totals=totals)


@bp.route("/cart/add", methods=["POST"])
@login_required
def cart_add():
    pid = oid(request.form.get("product_id"))
    qty = max(to_int(request.form.get("qty"), 1), 1)
    product = mongo.db.products.find_one({"_id": pid, "active": True})
    if not product:
        abort(404)
    if product["stock"] <= 0:
        flash("Sorry, this product is out of stock.", "warning")
        return redirect(request.referrer or url_for("shop.products"))

    items = _cart_items()
    for it in items:
        if it["product_id"] == pid:
            it["qty"] = min(it["qty"] + qty, product["stock"])
            break
    else:
        items.append({"product_id": pid, "qty": min(qty, product["stock"])})
    _save_cart(items)
    if request.form.get("buy_now"):
        return redirect(url_for("shop.checkout"))
    flash(f"Added {product['name']} to your cart.", "success")
    return redirect(request.referrer or url_for("shop.cart"))


@bp.route("/cart/update", methods=["POST"])
@login_required
def cart_update():
    pid = oid(request.form.get("product_id"))
    qty = to_int(request.form.get("qty"), 1)
    product = mongo.db.products.find_one({"_id": pid}, {"stock": 1})
    items = _cart_items()
    if qty <= 0 or not product:
        items = [i for i in items if i["product_id"] != pid]
    else:
        for it in items:
            if it["product_id"] == pid:
                it["qty"] = min(qty, max(product["stock"], 1))
                if qty > product["stock"]:
                    flash(f"Only {product['stock']} in stock.", "warning")
    _save_cart(items)
    return redirect(url_for("shop.cart"))


@bp.route("/cart/remove", methods=["POST"])
@login_required
def cart_remove():
    pid = oid(request.form.get("product_id"))
    _save_cart([i for i in _cart_items() if i["product_id"] != pid])
    flash("Item removed from cart.", "info")
    return redirect(url_for("shop.cart"))


# ---------------------------------------------------------------- checkout

ADDRESS_FIELDS = ["full_name", "phone", "line1", "line2", "city", "state", "zip", "country"]


@bp.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    lines, totals = cart_details(g.user["_id"])
    if not lines:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("shop.cart"))

    address = dict(g.user.get("address") or {})
    address.setdefault("full_name", g.user.get("name", ""))
    address.setdefault("phone", g.user.get("phone", ""))

    if request.method == "POST":
        address = {f: request.form.get(f, "").strip() for f in ADDRESS_FIELDS}
        missing = [f for f in ADDRESS_FIELDS if f != "line2" and not address[f]]
        if missing:
            flash("Please fill in all required address fields.", "danger")
            return render_template("shop/checkout.html", lines=lines, totals=totals, address=address)
        if not all(l["available"] for l in lines):
            flash("Some items exceed available stock. Please update your cart.", "danger")
            return redirect(url_for("shop.cart"))

        # Reserve stock atomically per item; roll back if any item fails.
        reserved = []
        for l in lines:
            res = mongo.db.products.update_one(
                {"_id": l["product"]["_id"], "active": True, "stock": {"$gte": l["qty"]}},
                {"$inc": {"stock": -l["qty"]}})
            if res.modified_count != 1:
                for pid, q in reserved:
                    mongo.db.products.update_one({"_id": pid}, {"$inc": {"stock": q}})
                flash(f"'{l['product']['name']}' just went out of stock. Please review your cart.",
                      "danger")
                return redirect(url_for("shop.cart"))
            reserved.append((l["product"]["_id"], l["qty"]))

        now = datetime.utcnow()
        order = {
            "order_number": now.strftime("ORD%y%m%d") + secrets.token_hex(3).upper(),
            "user_id": g.user["_id"], "user_name": g.user["name"], "user_email": g.user["email"],
            "items": [{"product_id": l["product"]["_id"], "name": l["product"]["name"],
                       "image_url": l["product"].get("image_url", ""),
                       "price": l["product"]["price"], "qty": l["qty"],
                       "subtotal": l["subtotal"]} for l in lines],
            **totals,
            "address": address,
            "payment_method": request.form.get("payment_method", "cod"),
            "notes": request.form.get("notes", "").strip(),
            "status": "pending",
            "status_history": [{"status": "pending", "at": now, "by": "customer"}],
            "created_at": now, "updated_at": now,
        }
        res = mongo.db.orders.insert_one(order)
        mongo.db.carts.update_one({"user_id": g.user["_id"]}, {"$set": {"items": []}})
        if request.form.get("save_address"):
            mongo.db.users.update_one({"_id": g.user["_id"]},
                                      {"$set": {"address": address, "phone": address["phone"]}})
        flash(f"Order {order['order_number']} placed successfully!", "success")
        return redirect(url_for("shop.order_detail", order_id=res.inserted_id))

    return render_template("shop/checkout.html", lines=lines, totals=totals, address=address)


# ------------------------------------------------------------------ orders

@bp.route("/orders")
@login_required
def orders():
    status = request.args.get("status", "")
    query = {"user_id": g.user["_id"]}
    if status:
        query["status"] = status
    page = to_int(request.args.get("page"), 1)
    pager = Pagination(page, 10, mongo.db.orders.count_documents(query))
    items = list(mongo.db.orders.find(query).sort("created_at", -1)
                 .skip(pager.skip).limit(pager.per_page))
    return render_template("shop/orders.html", orders=items, pager=pager, status=status)


@bp.route("/orders/<order_id>")
@login_required
def order_detail(order_id):
    order = mongo.db.orders.find_one({"_id": oid(order_id), "user_id": g.user["_id"]})
    if not order:
        abort(404)
    return render_template("shop/order_detail.html", order=order)


@bp.route("/orders/<order_id>/cancel", methods=["POST"])
@login_required
def order_cancel(order_id):
    now = datetime.utcnow()
    order = mongo.db.orders.find_one_and_update(
        {"_id": oid(order_id), "user_id": g.user["_id"], "status": "pending"},
        {"$set": {"status": "cancelled", "updated_at": now},
         "$push": {"status_history": {"status": "cancelled", "at": now, "by": "customer"}}})
    if not order:
        flash("Only pending orders can be cancelled.", "warning")
    else:
        for it in order["items"]:
            mongo.db.products.update_one({"_id": it["product_id"]}, {"$inc": {"stock": it["qty"]}})
        flash("Order cancelled.", "info")
    return redirect(url_for("shop.order_detail", order_id=order_id))


# --------------------------------------------------------------- dashboard

@bp.route("/dashboard")
@login_required
def dashboard():
    uid = g.user["_id"]
    agg = list(mongo.db.orders.aggregate([
        {"$match": {"user_id": uid}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}, "total": {"$sum": "$total"}}},
    ]))
    by_status = {a["_id"]: a for a in agg}
    stats = {
        "orders": sum(a["count"] for a in agg),
        "spent": sum(a["total"] for a in agg if a["_id"] != "cancelled"),
        "active": sum(by_status.get(s, {}).get("count", 0)
                      for s in ("pending", "processing", "shipped")),
        "delivered": by_status.get("delivered", {}).get("count", 0),
    }
    recent = list(mongo.db.orders.find({"user_id": uid}).sort("created_at", -1).limit(5))
    return render_template("shop/dashboard.html", stats=stats, recent=recent)


@bp.route("/dashboard/profile", methods=["POST"])
@login_required
def update_profile():
    name = request.form.get("name", "").strip()
    if len(name) < 2:
        flash("Name must be at least 2 characters.", "danger")
        return redirect(url_for("shop.dashboard"))
    address = {f: request.form.get(f, "").strip() for f in ADDRESS_FIELDS if f not in ("full_name", "phone")}
    address["full_name"] = name
    address["phone"] = request.form.get("phone", "").strip()
    mongo.db.users.update_one({"_id": g.user["_id"]}, {"$set": {
        "name": name, "phone": address["phone"], "address": address}})
    flash("Profile updated.", "success")
    return redirect(url_for("shop.dashboard"))


@bp.route("/dashboard/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current", "")
    new = request.form.get("new", "")
    if not check_password_hash(g.user["password"], current):
        flash("Current password is incorrect.", "danger")
    elif len(new) < 6:
        flash("New password must be at least 6 characters.", "danger")
    elif new != request.form.get("confirm", ""):
        flash("New passwords do not match.", "danger")
    else:
        mongo.db.users.update_one({"_id": g.user["_id"]},
                                  {"$set": {"password": generate_password_hash(new)}})
        flash("Password changed.", "success")
    return redirect(url_for("shop.dashboard"))

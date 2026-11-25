from flask import Flask, g, render_template, request, session
from markupsafe import Markup

from config import Config
from db import ORDER_STATUSES, get_settings, init_db, mongo
from utils import check_csrf, csrf_token, discount_pct


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    init_db(app)

    from blueprints.admin import bp as admin_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.shop import bp as shop_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    @app.before_request
    def load_context():
        g.user = g.admin = None
        if request.endpoint == "static":
            return
        g.settings = get_settings()  # load first so error pages can render
        check_csrf()
        # Customer session and admin session are independent of each other.
        if session.get("user_id"):
            from bson import ObjectId
            g.user = mongo.db.users.find_one({"_id": ObjectId(session["user_id"]), "active": True})
            if not g.user:
                session.pop("user_id", None)
        if session.get("admin_id"):
            from bson import ObjectId
            g.admin = mongo.db.users.find_one(
                {"_id": ObjectId(session["admin_id"]), "role": "admin", "active": True})
            if not g.admin:
                session.pop("admin_id", None)

    @app.context_processor
    def inject_globals():
        cart_count = 0
        if g.get("user"):
            cart = mongo.db.carts.find_one({"user_id": g.user["_id"]}, {"items": 1})
            cart_count = sum(i["qty"] for i in (cart or {}).get("items", []))
        nav_categories = []
        if getattr(g, "settings", None) is not None and request.blueprint != "admin":
            nav_categories = list(mongo.db.categories.find({"active": True}).sort("name", 1))
        return {
            "csrf_token": csrf_token,
            "csrf_field": lambda: Markup(
                f'<input type="hidden" name="_csrf" value="{csrf_token()}">'),
            "settings": g.get("settings", {}),
            "cart_count": cart_count,
            "nav_categories": nav_categories,
            "ORDER_STATUSES": ORDER_STATUSES,
        }

    @app.template_filter("money")
    def money(value):
        symbol = g.get("settings", {}).get("currency_symbol", "$")
        return f"{symbol}{float(value or 0):,.2f}"

    app.add_template_global(discount_pct, "discount_pct")

    @app.template_filter("dt")
    def dt(value, fmt="%d %b %Y, %H:%M"):
        return value.strftime(fmt) if value else ""

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, message=e.description), 400

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)

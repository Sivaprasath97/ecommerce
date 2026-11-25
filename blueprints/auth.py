from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from db import mongo
from utils import safe_next

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if not g.settings.get("allow_registration", True):
        flash("New registrations are currently closed.", "warning")
        return redirect(url_for("shop.home"))
    if g.user:
        return redirect(url_for("shop.dashboard"))

    form = request.form
    if request.method == "POST":
        name = form.get("name", "").strip()
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        confirm = form.get("confirm", "")
        errors = []
        if len(name) < 2:
            errors.append("Name must be at least 2 characters.")
        if "@" not in email or "." not in email:
            errors.append("Enter a valid email address.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if not errors:
            try:
                res = mongo.db.users.insert_one({
                    "name": name, "email": email,
                    "password": generate_password_hash(password),
                    "role": "customer", "active": True,
                    "phone": "", "address": {}, "created_at": datetime.utcnow(),
                })
                session.clear()
                session["user_id"] = str(res.inserted_id)
                flash(f"Welcome, {name}! Your account has been created.", "success")
                return redirect(url_for("shop.home"))
            except DuplicateKeyError:
                errors.append("An account with that email already exists.")
        for e in errors:
            flash(e, "danger")
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("shop.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = mongo.db.users.find_one({"email": email})
        if not user or not check_password_hash(user["password"], password):
            flash("Invalid email or password.", "danger")
        elif not user.get("active", True):
            flash("Your account has been disabled. Please contact support.", "danger")
        else:
            admin_id = session.get("admin_id")
            session.clear()
            if admin_id:
                session["admin_id"] = admin_id
            session["user_id"] = str(user["_id"])
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(safe_next(request.args.get("next"), url_for("shop.home")))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("shop.home"))

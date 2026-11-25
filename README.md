# ShopEase — Flask + MongoDB Atlas e-commerce

A storefront for customers and a separate admin panel at `/admin`. Store content and settings live in MongoDB, so admin changes show on the storefront right away.

## Setup

1. Open `.env` and replace `<cluster>` in `MONGO_URI` with your Atlas cluster host.
   In Atlas, go to **Database → Connect → Drivers** and copy the host, for example `cluster0.abcde.mongodb.net`.
2. In Atlas **Network Access**, allow your IP address.
3. Install and run:

   ```bash
   python -m venv venv
   venv\Scripts\activate          # Windows
   pip install -r requirements.txt
   python app.py
   ```

4. Open http://127.0.0.1:5000. The admin panel is at http://127.0.0.1:5000/admin.

The first run creates the indexes, the default store settings, an admin account (`ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`) and some sample categories and products.

## Features

**Customer store**
- Authentication: register, login, logout and change password.
- Products: browse, search, filter by category, sort and page through results; product detail with related items.
- Cart: stored in MongoDB per user; add, update quantity and remove; capped at available stock.
- Checkout: shipping address and cash on delivery. Stock is reserved with an atomic `$gte` check and rolled back if any item fails.
- Orders: order history, status filter, order detail with a progress tracker and status history, and cancellation while the order is pending (stock is restored).
- Dashboard: order statistics, recent orders, and profile and address editing.

**Admin panel (`/admin`, separate login and session)**
- Dashboard: revenue, order, customer and product counts, a 14-day sales chart, orders by status and low-stock alerts.
- Products: create, edit and delete; upload an image or use an image URL; one-click active and featured toggles.
- Categories: create, edit, hide and delete.
- Orders: search and filter; change status with a note to the customer; cancelling an order restocks its items.
- Users: enable or disable accounts and promote or demote admins.
- **Store settings**: store name, brand color, announcement bar, banner title, subtitle and image, currency symbol, shipping fee, free-shipping threshold, tax rate, products per page, registration on or off, contact email and footer text.

## Structure

```
app.py               app factory, request context, filters
config.py            env-based config
db.py                Atlas connection, indexes, settings, seed data
utils.py             auth decorators, CSRF, pagination, totals, uploads
blueprints/auth.py   customer register/login/logout
blueprints/shop.py   products, cart, checkout, orders, dashboard
blueprints/admin.py  admin panel
templates/           Jinja templates (shop/, auth/, admin/)
static/              CSS and uploaded images
```

## Collections

`users`, `products`, `categories`, `carts`, `orders`, `settings`

## Production notes

- Change `SECRET_KEY` and the admin password, and use a strong Atlas database password.
- Run with a WSGI server such as `waitress-serve --port=8000 app:app` on Windows, or gunicorn on Linux.

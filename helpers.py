import os
import requests
import urllib.parse

from cs50 import SQL
from datetime import datetime, timezone
from flask import redirect, render_template, session
from functools import wraps


# Configure CS50 Library to use SQLite database
db = SQL("sqlite:///finance.db")

# Create new table, and index (for efficient search later on) to keep track of stock orders, by each user
db.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER, user_id NUMERIC NOT NULL, symbol TEXT NOT NULL, \
            shares NUMERIC NOT NULL, price NUMERIC NOT NULL, timestamp TEXT, PRIMARY KEY(id), \
            FOREIGN KEY(user_id) REFERENCES users(id))")
db.execute("CREATE INDEX IF NOT EXISTS orders_by_user_id_index ON orders (user_id)")
db.execute("CREATE TABLE IF NOT EXISTS open_orders (id INTEGER, user_id NUMERIC NOT NULL, symbol TEXT NOT NULL, \
            shares NUMERIC NOT NULL, order_price NUMERIC NOT NULL, timestamp TEXT, PRIMARY KEY(id), \
            FOREIGN KEY(user_id) REFERENCES users(id))")

def apology(message, code=400):
    """Render message as an apology to user."""
    def escape(s):
        """
        Escape special characters.

        https://github.com/jacebrowning/memegen#special-characters
        """
        for old, new in [("-", "--"), (" ", "-"), ("_", "__"), ("?", "~q"),
                         ("%", "~p"), ("#", "~h"), ("/", "~s"), ("\"", "''")]:
            s = s.replace(old, new)
        return s
    return render_template("apology.html", top=code, bottom=escape(message)), code


def login_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


def lookup(symbol):
    """Look up quote for symbol."""

    # Contact API
    try:
        api_key = os.environ.get("API_KEY")
        url = f"https://cloud.iexapis.com/stable/stock/{urllib.parse.quote_plus(symbol)}/quote?token={api_key}"
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException:
        return None

    # Parse response
    try:
        quote = response.json()
        return {
            "name": quote["companyName"],
            "price": float(quote["latestPrice"]),
            "symbol": quote["symbol"]
        }
    except (KeyError, TypeError, ValueError):
        return None


def usd(value):
    """Format value as USD."""
    return f"${value:,.2f}"


def time_now():
    """HELPER: get current UTC date and time"""
    now_utc = datetime.now(timezone.utc)
    return str(now_utc.date()) + ' @time ' + now_utc.time().strftime("%H:%M:%S")


def place_order_buy(user_id, symbol, shares, price, order_price, remain, time, owns):
    try:
        result = lookup(symbol)

        if order_price >= price:

                # deduct order cost from user's remaining balance
                db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)
                # log the transaction in history
                db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                                user_id, symbol, shares, price, time_now())
                # delete order from open_orders table
                db.execute("DELETE FROM open_orders WHERE order_price =?", order_price)
                return None
        
        elif order_price < price :
            # check if order price is equal to current market price
            while order_price < price :
                result = lookup(symbol)
                price = result["price"]
            
            #execute order
            if order_price >= price:

                # deduct order cost from user's remaining balance
                db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)
                # log the transaction in history
                db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                                user_id, symbol, shares, price, time_now())
                # delete order from open_orders table
                db.execute("DELETE FROM open_orders WHERE order_price =?", order_price)
                return None

    except (KeyError, TypeError, ValueError):
    # Handle errors gracefully and log them
        return None
    

def place_order_sell(user_id, symbol, shares, price, order_price, remain, time, owns):
    try:
        result = lookup(symbol)
        if owns[symbol] < shares:
            return render_template("sell.html", invalid=True, symbol=symbol, owns = owns.keys())

        if order_price <= price:

                db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)
                # Log the transaction into history
                db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                            user_id, symbol, -shares, price, time_now())
                db.execute("DELETE FROM open_orders WHERE order_price =?", order_price)
                return None
        
        elif order_price > price :
            # check if order price is equal to current market price
            while order_price > price :
                result = lookup(symbol)
                price = result["price"]
            
            #execute order
            if order_price <= price:

                db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)
                # Log the transaction into history
                db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                            user_id, symbol, -shares, price, time_now())
                db.execute("DELETE FROM open_orders WHERE order_price =?", order_price)
                return None

    except (KeyError, TypeError, ValueError):
    # Handle errors gracefully and log them
        return apology("error in order", 403)
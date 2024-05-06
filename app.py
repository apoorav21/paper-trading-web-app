import os
import threading

from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from tempfile import mkdtemp
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from helpers import apology, login_required, lookup, usd, place_order_sell, place_order_buy
from datetime import datetime, timezone

# Configure application
app = Flask(__name__)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True


# Ensure responses aren't cached
@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


# Custom filter
app.jinja_env.filters["usd"] = usd

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_FILE_DIR"] = mkdtemp()
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

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


# Make sure API key is set
if not os.environ.get("API_KEY"):
    raise RuntimeError("API_KEY not set")

@app.route("/")
@login_required
def index():
    """Show portfolio of stocks"""
    owns = own_shares()
    total = 0
    for symbol, shares in owns.items():
        result = lookup(symbol)
        name, price = result["name"], result["price"]
        stock_value = shares * price
        total += stock_value
        owns[symbol] = (name, shares, usd(price), usd(stock_value))
    cash = db.execute("SELECT cash FROM users WHERE id = ? ", session["user_id"])[0]['cash']
    total += cash
    return render_template("index.html", owns=owns, cash= usd(cash), total = usd(total))


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    """Buy shares of stock"""
    if request.method == "GET":
        return render_template("buy.html")

    result = lookup(request.form.get("symbol"))
    if not result:
        return render_template("buy.html", invalid=True, symbol = request.form.get("symbol"))

    name = result["name"]
    price = result["price"]
    symbol = result["symbol"]
    shares = int(request.form.get("shares"))
    user_id = session["user_id"]
    cash = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]['cash']
    # check if user can afford the purchase
    remain = cash - price * shares
    if remain < 0:
        return apology("Insufficient Cash. Failed Purchase.")

    # deduct order cost from user's remaining balance
    db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)

    db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                     user_id, symbol, shares, price, time_now())

    return redirect("/")

@app.route("/history")
@login_required
def history():
    """Show history of transactions"""
    rows = db.execute("SELECT symbol, shares, price, timestamp FROM orders WHERE user_id = ?", session["user_id"])
    return render_template("history.html", rows = rows)

@app.route("/add_cash", methods=["GET", "POST"])
@login_required
def add_cash():
    """Add more cash to total balance"""
    if request.method == "GET":
        return render_template("add.html")
    else :
        new_cash = int(request.form.get("new_cash"))

        if not new_cash:
            return apology("must provide money", 403)

        user_id = session["user_id"]
        cash = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]['cash']
        updated_cash = cash + new_cash
        db.execute("UPDATE users SET cash = ? WHERE id = ?", updated_cash, user_id)
        return redirect("/")

@app.route("/order", methods=["GET", "POST"])
@login_required
def place_order():
    if request.method == "GET":
        # show open orders
        rows = db.execute("SELECT symbol, shares, order_price, timestamp FROM open_orders WHERE user_id = ?", session["user_id"])
        return render_template("order.html", rows = rows)

    result = lookup(request.form.get("symbol"))
    
    if not result:
        return render_template("order.html", invalid=True, symbol = request.form.get("symbol"))
    
    user_id = session["user_id"]  # Get the user's ID
    symbol = result["symbol"]
    shares = int(request.form.get("shares"))
    price = result["price"]
    order_price = float(request.form.get("price"))
    cash = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]['cash']
    remain = cash - price * shares
    time = time_now()
    owns = own_shares()
    api_request_args = (user_id, symbol, shares, price, order_price, remain, time, owns)
    #check wheather to buy or sell
    action = request.form.get("task")
    if action == "buy":
        #log open orders
        db.execute("INSERT INTO open_orders (user_id, symbol, shares, order_price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                        user_id, symbol, shares, order_price, time_now())
        # Enqueue the task
        background_thread = threading.Thread(target=place_order_buy, args=api_request_args)
        background_thread.start()
    elif action == "sell" :
        #log open orders
        db.execute("INSERT INTO open_orders (user_id, symbol, shares, order_price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                        user_id, symbol, -shares, order_price, time_now())
        # Enqueue the task
        background_thread = threading.Thread(target=place_order_sell, args=api_request_args)
        background_thread.start()

    # show open orders
    rows = db.execute("SELECT symbol, shares, order_price, timestamp FROM open_orders WHERE user_id = ?", session["user_id"])
    return render_template("order.html", rows = rows)



@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    session.clear()

    #clear open  orders
    db.execute("DELETE FROM open_orders")

    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            return apology("must provide username", 403)

        # Ensure password was submitted
        elif not request.form.get("password"):
            return apology("must provide password", 403)

        # Query database for username
        rows = db.execute("SELECT * FROM users WHERE username = ?", request.form.get("username"))

        # Ensure username exists and password is correct
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], request.form.get("password")):
            return apology("invalid username and/or password", 403)

        # Remember which user has logged in
        session["user_id"] = rows[0]["id"]

        # Redirect user to home page
        return redirect("/")


    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    #clear open  orders
    db.execute("DELETE FROM open_orders")

    # Redirect user to login form
    return redirect("/")


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    """Get stock quote."""
    if request.method == "GET":
        return render_template("quote.html")
    symbol = request.form.get("symbol")
    result = lookup(symbol)
    if not result:
        return render_template("quote.html", invalid=True, symbol = symbol)
    return render_template("quoted.html", name = result["name"], price = usd(result["price"]), symbol = result["symbol"])


@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""
    if request.method == "GET":
        return render_template("register.html")
    # check username and password
    username = request.form.get("username")
    password = request.form.get("password")
    confirmation = request.form.get("confirmation")
    if username == "" or len(db.execute('SELECT username FROM users WHERE username = ?', username)) > 0:
        return apology("Invalid Username: Blank, or already exists")
    if password == "" or password != confirmation:
        return apology("Invalid Password: Blank, or does not match")
    # Add new user to users db (includes: username and HASH of password)
    db.execute('INSERT INTO users (username, hash) \
            VALUES(?, ?)', username, generate_password_hash(password))
    # Query database for username
    rows = db.execute("SELECT * FROM users WHERE username = ?", username)
    # Log user in, i.e. Remember that this user has logged in
    session["user_id"] = rows[0]["id"]
    # Redirect user to home page
    return redirect("/")


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    """Sell shares of stock; Similar to /buy, with negative # shares"""
    owns = own_shares()
    if request.method == "GET":
        return render_template("sell.html", owns = owns.keys())

    symbol = request.form.get("symbol")
    shares = int(request.form.get("shares"))
    # check whether there are sufficient shares to sell
    if owns[symbol] < shares:
        return render_template("sell.html", invalid=True, symbol=symbol, owns = owns.keys())
    # Execute sell transaction: look up sell price, and add fund to cash,
    result = lookup(symbol)
    user_id = session["user_id"]
    cash = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]['cash']
    price = result["price"]
    remain = cash + price * shares
    db.execute("UPDATE users SET cash = ? WHERE id = ?", remain, user_id)
    # Log the transaction into orders
    db.execute("INSERT INTO orders (user_id, symbol, shares, price, timestamp) VALUES (?, ?, ?, ?, ?)", \
                                     user_id, symbol, -shares, price, time_now())

    return redirect("/")


def errorhandler(e):
    """Handle error"""
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return apology(e.name, e.code)


# Listen for errors
for code in default_exceptions:
        app.errorhandler(code)(errorhandler)


def own_shares():
    """Helper function: Which stocks the user owns, and numbers of shares owned. Return: dictionary {symbol: qty}"""
    user_id = session["user_id"]
    owns = {}
    query = db.execute("SELECT symbol, shares FROM orders WHERE user_id = ?", user_id)
    for q in query:
        symbol, shares = q["symbol"], q["shares"]
        owns[symbol] = owns.setdefault(symbol, 0) + shares
    # filter zero-share stocks
    owns = {k: v for k, v in owns.items() if v != 0}
    return owns

def time_now():
    """HELPER: get current UTC date and time"""
    now_utc = datetime.now(timezone.utc)
    return str(now_utc.date()) + ' @time ' + now_utc.time().strftime("%H:%M:%S")




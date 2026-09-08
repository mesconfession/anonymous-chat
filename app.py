140
from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO
import sqlite3
import random
import string
import time

app = Flask(__name__)
app.secret_key = "change-this-secret-later"

socketio = SocketIO(app, cors_allowed_origins="*")

online_users = {}

DB = "chat.db"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            gender TEXT,
            online INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            message TEXT,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()
def create_user(gender):
    user_id = "user_" + "".join(
        random.choices(string.ascii_lowercase + string.digits, k=8)
    )

    conn = get_db()

    conn.execute(
        "INSERT INTO users (user_id, gender, online) VALUES (?, ?, 1)",
        (user_id, gender)
    )

    conn.commit()
    conn.close()

    return user_id


def find_match(user_id, gender):
    opposite = "girl" if gender == "boy" else "boy"

    for other_id, data in online_users.items():
        if other_id != user_id and data["gender"] == opposite:
            return other_id

    return None
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    gender = request.form.get("gender")

    if gender not in ["boy", "girl"]:
        return redirect(url_for("home"))

    user_id = create_user(gender)

    session["user_id"] = user_id
    session["gender"] = gender

    return redirect(url_for("chat"))


@app.route("/chat")
def chat():
    if "user_id" not in session:
        return redirect(url_for("home"))

    return render_template(
        "chat.html",
        user_id=session["user_id"]
    )
def save_match(user1, user2):
    conn = get_db()

    conn.execute(
        "INSERT INTO messages (sender, receiver, message, created_at) VALUES (?, ?, ?, ?)",
        (user1, user2, "", int(time.time()))
    )

    conn.commit()
    conn.close()


def match_users(user_id):
    if user_id not in online_users:
        return

    gender = online_users[user_id]["gender"]
    partner = find_match(user_id, gender)

    if not partner:
        socketio.emit("waiting", to=online_users[user_id]["sid"])
        return

    online_users[user_id]["partner"] = partner
    online_users[partner]["partner"] = user_id
    socketio.emit(
        "matched",
        {"partner": partner},
        to=online_users[user_id]["sid"]
    )

    socketio.emit(
        "matched",
        {"partner": user_id},
        to=online_users[partner]["sid"]
    )


@socketio.on("connect")
def handle_connect():
    user_id = session.get("user_id")

    if not user_id:
        return

    gender = session.get("gender")

    online_users[user_id] = {
        "gender": gender,
        "sid": request.sid,
        "partner": None
    }

    conn = get_db()
    conn.execute(
        "UPDATE users SET online = 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()

    match_users(user_id)


@socketio.on("disconnect")
def handle_disconnect():
    user_id = session.get("user_id")

    if not user_id:
        return

    data = online_users.get(user_id)

    if data:
        partner = data.get("partner")

        if partner and partner in online_users:
            online_users[partner]["partner"] = None

            socketio.emit(
                "partner_offline",
                to=online_users[partner]["sid"]
            )

        del online_users[user_id]

    conn = get_db()
    conn.execute(
        "UPDATE users SET online = 0 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()
@socketio.on("send_message")
def handle_message(data):
    user_id = session.get("user_id")

    if not user_id or user_id not in online_users:
        return

    partner = online_users[user_id].get("partner")

    if not partner:
        return

    message = data.get("message", "").strip()

    if not message or len(message) > 1000:
        return

    conn = get_db()

    conn.execute(
        """
        INSERT INTO messages
        (sender, receiver, message, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, partner, message, int(time.time()))
    )

    conn.commit()
    conn.close()

    if partner in online_users:
        socketio.emit(
            "message",
            {
                "sender": user_id,
                "message": message
            },
            to=online_users[partner]["sid"]
        )
@socketio.on("load_messages")
def load_messages():
    user_id = session.get("user_id")

    if not user_id or user_id not in online_users:
        return

    partner = online_users[user_id].get("partner")

    if not partner:
        return

    conn = get_db()

    rows = conn.execute(
        """
        SELECT sender, message
        FROM messages
        WHERE
        (sender = ? AND receiver = ?)
        OR
        (sender = ? AND receiver = ?)
        ORDER BY id ASC
        """,
        (user_id, partner, partner, user_id)
    ).fetchall()

    conn.close()

    for row in rows:
        if row["message"]:
            socketio.emit(
                "message",
                {
                    "sender": row["sender"],
                    "message": row["message"]
                },
                to=request.sid
            )
@socketio.on("next")
def handle_next():
    user_id = session.get("user_id")

    if not user_id or user_id not in online_users:
        return

    old_partner = online_users[user_id].get("partner")

    if old_partner and old_partner in online_users:
        online_users[old_partner]["partner"] = None

        socketio.emit(
            "partner_offline",
            to=online_users[old_partner]["sid"]
        )

    online_users[user_id]["partner"] = None

    match_users(user_id)
@app.route("/report", methods=["POST"])
def report():
    reporter = session.get("user_id")
    reported = request.form.get("reported")
    reason = request.form.get("reason", "").strip()

    if not reporter or not reported or not reason:
        return "Invalid report", 400

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter TEXT,
            reported TEXT,
            reason TEXT,
            created_at INTEGER
        )
        """
    )

    conn.execute(
        """
        INSERT INTO reports
        (reporter, reported, reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (reporter, reported, reason, int(time.time()))
    )

    conn.commit()
    conn.close()

    return "Report submitted"
if __name__ == "__main__":
    init_db()
    print("Anonymous Chat Server Started")
    print("Open http://127.0.0.1:5000")
    socketio.run(app, host="0.0.0.0", port=5001)

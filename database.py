import sqlite3
from contextlib import closing
from config import DATABASE_PATH

def connect():
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with closing(connect()) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          telegram_id INTEGER UNIQUE NOT NULL,
          username TEXT,
          first_name TEXT,
          balance REAL NOT NULL DEFAULT 0,
          referrer_id INTEGER,
          blocked INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          telegram_id INTEGER NOT NULL,
          kind TEXT NOT NULL,
          amount REAL NOT NULL,
          note TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        con.commit()

def upsert_user(user, referrer_id=None):
    with closing(connect()) as con:
        con.execute("""
        INSERT INTO users(telegram_id,username,first_name,referrer_id)
        VALUES(?,?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
          username=excluded.username, first_name=excluded.first_name
        """, (user.id, user.username, user.first_name, referrer_id))
        con.commit()

def get_user(tg_id):
    with closing(connect()) as con:
        return con.execute(
            "SELECT * FROM users WHERE telegram_id=?", (tg_id,)
        ).fetchone()

def change_balance(tg_id, amount, kind, note=""):
    with closing(connect()) as con:
        row = con.execute(
            "SELECT balance FROM users WHERE telegram_id=?", (tg_id,)
        ).fetchone()
        if not row:
            return False, 0
        new_balance = float(row["balance"]) + float(amount)
        if new_balance < 0:
            return False, float(row["balance"])
        con.execute(
            "UPDATE users SET balance=? WHERE telegram_id=?",
            (new_balance, tg_id)
        )
        con.execute(
            "INSERT INTO transactions(telegram_id,kind,amount,note) VALUES(?,?,?,?)",
            (tg_id, kind, amount, note)
        )
        con.commit()
        return True, new_balance

def set_blocked(tg_id, value):
    with closing(connect()) as con:
        con.execute(
            "UPDATE users SET blocked=? WHERE telegram_id=?",
            (int(value), tg_id)
        )
        con.commit()

def all_users():
    with closing(connect()) as con:
        return con.execute("SELECT * FROM users ORDER BY id DESC").fetchall()

def stats():
    with closing(connect()) as con:
        total = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        blocked = con.execute(
            "SELECT COUNT(*) c FROM users WHERE blocked=1"
        ).fetchone()["c"]
        balance = con.execute(
            "SELECT COALESCE(SUM(balance),0) s FROM users"
        ).fetchone()["s"]
        return total, blocked, float(balance)

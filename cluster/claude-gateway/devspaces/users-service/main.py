import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Users Service")

DB_PATH = os.getenv("DB_PATH", "users-service/users.db")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id    TEXT PRIMARY KEY,
                name  TEXT NOT NULL,
                email TEXT NOT NULL
            )
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


@app.on_event("startup")
def startup():
    init_db()


class UserCreate(BaseModel):
    name: str
    email: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "users"}


@app.get("/users")
def list_users():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/users", status_code=201)
def create_user(user: UserCreate):
    uid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, name, email) VALUES (?, ?, ?)",
            (uid, user.name, user.email),
        )
    return {"id": uid, "name": user.name, "email": user.email}


@app.get("/users/{user_id}")
def get_user(user_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row_to_dict(row)


@app.put("/users/{user_id}")
def update_user(user_id: str, update: UserUpdate):
    fields = update.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            (*fields.values(), user_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_dict(row)


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")

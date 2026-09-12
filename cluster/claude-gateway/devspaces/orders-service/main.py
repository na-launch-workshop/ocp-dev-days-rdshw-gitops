import sqlite3
import uuid
import os
from contextlib import contextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Orders Service")

DB_PATH = os.getenv("DB_PATH", "orders-service/orders.db")

USERS_URL     = os.getenv("USERS_SERVICE_URL",     "http://localhost:8001")
PRODUCTS_URL  = os.getenv("PRODUCTS_SERVICE_URL",  "http://localhost:8002")
INVENTORY_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8003")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                user_name    TEXT NOT NULL,
                product_id   TEXT NOT NULL,
                product_name TEXT NOT NULL,
                unit_price   REAL NOT NULL,
                quantity     INTEGER NOT NULL,
                total        REAL NOT NULL,
                status       TEXT NOT NULL DEFAULT 'confirmed'
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


def _get(url: str, not_found_msg: str) -> dict:
    try:
        r = httpx.get(url, timeout=5)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {e}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=not_found_msg)
    r.raise_for_status()
    return r.json()


@app.on_event("startup")
def startup():
    init_db()


class OrderCreate(BaseModel):
    user_id: str
    product_id: str
    quantity: int


class OrderUpdate(BaseModel):
    status: Optional[str] = None
    quantity: Optional[int] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders"}


@app.get("/orders")
def list_orders():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders").fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/orders", status_code=201)
def create_order(order: OrderCreate):
    user    = _get(f"{USERS_URL}/users/{order.user_id}",       "User not found")
    product = _get(f"{PRODUCTS_URL}/products/{order.product_id}", "Product not found")

    try:
        r = httpx.post(
            f"{INVENTORY_URL}/inventory/{order.product_id}/reserve",
            json={"quantity": order.quantity},
            timeout=5,
        )
        if r.status_code == 409:
            raise HTTPException(status_code=409, detail=r.json()["detail"])
        r.raise_for_status()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Inventory service unavailable: {e}")

    oid   = str(uuid.uuid4())
    total = product["price"] * order.quantity

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO orders
               (id, user_id, user_name, product_id, product_name, unit_price, quantity, total, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (oid, order.user_id, user["name"],
             order.product_id, product["name"],
             product["price"], order.quantity, total, "confirmed"),
        )
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    return row_to_dict(row)


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row_to_dict(row)


@app.put("/orders/{order_id}")
def update_order(order_id: str, update: OrderUpdate):
    fields = update.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE orders SET {set_clause} WHERE id = ?",
            (*fields.values(), order_id),
        )
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return row_to_dict(row)


@app.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Order not found")

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Inventory Service")

DB_PATH = os.getenv("DB_PATH", "inventory-service/inventory.db")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                product_id TEXT PRIMARY KEY,
                quantity   INTEGER NOT NULL,
                location   TEXT NOT NULL
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


class InventoryCreate(BaseModel):
    product_id: str
    quantity: int
    location: str


class InventoryUpdate(BaseModel):
    quantity: Optional[int] = None
    location: Optional[str] = None


class ReserveRequest(BaseModel):
    quantity: int


@app.get("/health")
def health():
    return {"status": "ok", "service": "inventory"}


@app.get("/inventory")
def list_inventory():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM inventory").fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/inventory", status_code=201)
def create_inventory(item: InventoryCreate):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM inventory WHERE product_id = ?", (item.product_id,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Inventory entry already exists for this product")
        conn.execute(
            "INSERT INTO inventory (product_id, quantity, location) VALUES (?, ?, ?)",
            (item.product_id, item.quantity, item.location),
        )
    return item.model_dump()


@app.get("/inventory/{product_id}")
def get_inventory(product_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Inventory not found")
    return row_to_dict(row)


@app.put("/inventory/{product_id}")
def update_inventory(product_id: str, update: InventoryUpdate):
    fields = update.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Inventory not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE inventory SET {set_clause} WHERE product_id = ?",
            (*fields.values(), product_id),
        )
        row = conn.execute(
            "SELECT * FROM inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
    return row_to_dict(row)


@app.post("/inventory/{product_id}/reserve")
def reserve_stock(product_id: str, req: ReserveRequest):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Inventory not found")
        current = row["quantity"]
        if current < req.quantity:
            raise HTTPException(status_code=409, detail=f"Insufficient stock: {current} available")
        conn.execute(
            "UPDATE inventory SET quantity = quantity - ? WHERE product_id = ?",
            (req.quantity, product_id),
        )
        row = conn.execute(
            "SELECT * FROM inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
    return row_to_dict(row)


@app.delete("/inventory/{product_id}", status_code=204)
def delete_inventory(product_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM inventory WHERE product_id = ?", (product_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Inventory not found")

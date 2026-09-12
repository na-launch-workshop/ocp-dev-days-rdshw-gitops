import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Products Service")

DB_PATH = os.getenv("DB_PATH", "products-service/products.db")


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT NOT NULL,
                price       REAL NOT NULL
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


class ProductCreate(BaseModel):
    name: str
    description: str
    price: float


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "products"}


@app.get("/products")
def list_products():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM products").fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/products", status_code=201)
def create_product(product: ProductCreate):
    pid = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO products (id, name, description, price) VALUES (?, ?, ?, ?)",
            (pid, product.name, product.description, product.price),
        )
    return {"id": pid, **product.model_dump()}


@app.get("/products/{product_id}")
def get_product(product_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return row_to_dict(row)


@app.put("/products/{product_id}")
def update_product(product_id: str, update: ProductUpdate):
    fields = update.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Product not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE products SET {set_clause} WHERE id = ?",
            (*fields.values(), product_id),
        )
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return row_to_dict(row)


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Product not found")

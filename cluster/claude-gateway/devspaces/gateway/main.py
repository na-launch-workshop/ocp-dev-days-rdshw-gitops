"""
API Gateway — thin reverse-proxy that routes requests to the correct downstream service.
All four services are reachable through a single base URL (:8000).

Routes:
  /users/*      → Users Service    :8001
  /products/*   → Products Service :8002
  /inventory/*  → Inventory Service :8003
  /orders/*     → Orders Service   :8004
"""

from fastapi import FastAPI, Request, HTTPException, Response
import httpx
import os

app = FastAPI(title="API Gateway")

ROUTES: dict[str, str] = {
    "/users":     os.getenv("USERS_SERVICE_URL",     "http://localhost:8001"),
    "/products":  os.getenv("PRODUCTS_SERVICE_URL",  "http://localhost:8002"),
    "/inventory": os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8003"),
    "/orders":    os.getenv("ORDERS_SERVICE_URL",    "http://localhost:8004"),
}


def _resolve(path: str) -> tuple[str, str]:
    """Return (base_url, remainder_path) for the given request path."""
    for prefix, base in ROUTES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return base, path
    raise HTTPException(status_code=404, detail=f"No route for path: {path}")


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway", "routes": list(ROUTES.keys())}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    full_path = "/" + path
    base_url, downstream_path = _resolve(full_path)
    url = base_url + downstream_path

    params = dict(request.query_params)
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=url,
                params=params,
                content=body,
                headers=headers,
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Upstream error: {e}")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )

# Python Microservices Workshop

A hands-on example of 5 Python microservices communicating over REST, with full CRUD operations and SQLite persistence.

## Services

| Service | Port | Responsibility |
|---|---|---|
| **Gateway** | 8000 | Single entry point — routes all requests to the correct service |
| **Users** | 8001 | Create and manage user accounts |
| **Products** | 8002 | Create and manage the product catalog |
| **Inventory** | 8003 | Track stock levels per product |
| **Orders** | 8004 | Place orders — calls Users, Products, and Inventory internally |

Each service is its own container with its own SQLite database file (`/data/*.db`) mounted from a named Docker volume.

## Architecture

```
Client
  │
  ▼
Gateway :8000          ← only port exposed publicly
  │
  ├── /users/*     →  Users Service     :8001  (users.db)
  ├── /products/*  →  Products Service  :8002  (products.db)
  ├── /inventory/* →  Inventory Service :8003  (inventory.db)
  └── /orders/*    →  Orders Service    :8004  (orders.db)
                             │
                             ├── calls Users     (validate user exists)
                             ├── calls Products  (get name + price)
                             └── calls Inventory (reserve stock)
```

When you `POST /orders`, the Orders service makes HTTP calls to the other three services before writing the order — this is the cross-service communication pattern the workshop demonstrates.

## Running with Docker Compose

```bash
docker-compose up --build
```

Services start in dependency order enforced by health checks:

1. Users, Products, Inventory start in parallel
2. Orders starts once all three are healthy
3. Gateway starts once Orders is healthy

Data persists across restarts via named Docker volumes. To wipe all data:

```bash
docker-compose down -v
```

## Running Locally (no Docker)

```bash
pip install -r requirements.txt
./start-local.sh
```

## End-to-End Demo

With services running, execute the demo script to walk through the full flow:

```bash
./demo.sh
```

This will:
1. Create a user
2. Create a product
3. Add inventory for that product
4. Place an order (triggers cross-service calls)
5. Verify stock was decremented
6. List all orders

## API Reference

Every service exposes a Swagger UI at `/docs`:

- http://localhost:8000/docs — Gateway
- http://localhost:8001/docs — Users
- http://localhost:8002/docs — Products
- http://localhost:8003/docs — Inventory
- http://localhost:8004/docs — Orders

### Example requests (via gateway)

```bash
# Create a user
curl -X POST http://localhost:8000/users \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "email": "alice@example.com"}'

# Create a product
curl -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Laptop", "description": "Dev machine", "price": 1299.99}'

# Add inventory
curl -X POST http://localhost:8000/inventory \
  -H "Content-Type: application/json" \
  -d '{"product_id": "<product-id>", "quantity": 50, "location": "Warehouse A"}'

# Place an order
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<user-id>", "product_id": "<product-id>", "quantity": 2}'
```

## Project Structure

```
workshop-python-microservices/
├── docker-compose.yml
├── requirements.txt
├── start-local.sh          # run all services locally
├── demo.sh                 # end-to-end walkthrough script
├── gateway/
│   ├── Dockerfile
│   └── main.py
├── users-service/
│   ├── Dockerfile
│   └── main.py
├── products-service/
│   ├── Dockerfile
│   └── main.py
├── inventory-service/
│   ├── Dockerfile
│   └── main.py
└── orders-service/
    ├── Dockerfile
    └── main.py
```

## Stack

- **FastAPI** — REST framework with automatic Swagger UI
- **Uvicorn** — ASGI server
- **SQLite** — embedded database, one file per service
- **httpx** — HTTP client for cross-service calls
- **Docker Compose** — local orchestration with health checks and named volumes

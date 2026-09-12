#!/usr/bin/env bash
# Start all 5 services locally (without Docker).
# Requires: pip install -r requirements.txt

set -e
BASE="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE"

cleanup() {
  echo ""
  echo "Stopping all services..."
  kill $(jobs -p) 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting Users Service      on :8001"
uvicorn users-service.main:app --port 8001 --reload &

echo "Starting Products Service   on :8002"
uvicorn products-service.main:app --port 8002 --reload &

echo "Starting Inventory Service  on :8003"
uvicorn inventory-service.main:app --port 8003 --reload &

echo "Starting Orders Service     on :8004"
USERS_SERVICE_URL=http://localhost:8001 \
PRODUCTS_SERVICE_URL=http://localhost:8002 \
INVENTORY_SERVICE_URL=http://localhost:8003 \
uvicorn orders-service.main:app --port 8004 --reload &

echo "Starting API Gateway        on :8000"
USERS_SERVICE_URL=http://localhost:8001 \
PRODUCTS_SERVICE_URL=http://localhost:8002 \
INVENTORY_SERVICE_URL=http://localhost:8003 \
ORDERS_SERVICE_URL=http://localhost:8004 \
uvicorn gateway.main:app --port 8000 --reload &

echo ""
echo "All services running. Swagger UI available at:"
echo "  Gateway:   http://localhost:8000/docs"
echo "  Users:     http://localhost:8001/docs"
echo "  Products:  http://localhost:8002/docs"
echo "  Inventory: http://localhost:8003/docs"
echo "  Orders:    http://localhost:8004/docs"
echo ""
echo "Press Ctrl+C to stop all services."
wait

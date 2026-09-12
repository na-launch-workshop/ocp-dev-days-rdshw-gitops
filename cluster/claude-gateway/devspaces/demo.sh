#!/usr/bin/env bash
# End-to-end demo: create a user, product, inventory entry, then place an order.
# Assumes all services are already running (./start-local.sh or docker-compose up).

BASE_URL=${1:-http://localhost:8000}
echo "Using gateway: $BASE_URL"
echo ""

echo "=== 1. Create a user ==="
USER=$(curl -s -X POST "$BASE_URL/users" \
  -H "Content-Type: application/json" \
  -d '{"name":"Alice","email":"alice@example.com"}')
echo "$USER" | python3 -m json.tool
USER_ID=$(echo "$USER" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo ""
echo "=== 2. Create a product ==="
PRODUCT=$(curl -s -X POST "$BASE_URL/products" \
  -H "Content-Type: application/json" \
  -d '{"name":"Workshop Laptop","description":"High-performance dev machine","price":1299.99}')
echo "$PRODUCT" | python3 -m json.tool
PRODUCT_ID=$(echo "$PRODUCT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo ""
echo "=== 3. Add inventory for the product ==="
curl -s -X POST "$BASE_URL/inventory" \
  -H "Content-Type: application/json" \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":50,\"location\":\"Warehouse A\"}" \
  | python3 -m json.tool

echo ""
echo "=== 4. Place an order (calls Users, Products, and Inventory services) ==="
curl -s -X POST "$BASE_URL/orders" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"product_id\":\"$PRODUCT_ID\",\"quantity\":2}" \
  | python3 -m json.tool

echo ""
echo "=== 5. Check remaining inventory (stock should be 48) ==="
curl -s "$BASE_URL/inventory/$PRODUCT_ID" | python3 -m json.tool

echo ""
echo "=== 6. List all orders ==="
curl -s "$BASE_URL/orders" | python3 -m json.tool

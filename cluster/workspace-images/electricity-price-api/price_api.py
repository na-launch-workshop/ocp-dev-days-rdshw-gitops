from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

SCHEDULE = [
    (0,  6,  "Off-Peak", 0.079),
    (6,  16, "Mid-Peak", 0.124),
    (16, 21, "Peak",     0.189),
    (21, 22, "Mid-Peak", 0.124),
    (22, 24, "Off-Peak", 0.079),
]


@app.get("/price")
def get_price():
    hour = datetime.now(timezone.utc).hour
    for start, end, tier, price in SCHEDULE:
        if start <= hour < end:
            return JSONResponse(
                content={
                    "price": price,
                    "tier": tier,
                    "currency": "USD",
                    "unit": "kWh",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                headers={"Cache-Control": "no-store"},
            )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

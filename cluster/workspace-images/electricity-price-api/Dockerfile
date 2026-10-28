# Price API sidecar for the "Event Streaming with Apache Kafka" workshop (Task 6), shared by
# its Python and .NET versions. Built on-cluster into workspace-images/electricity-price-api
# by templates/buildconfig-electricity-price-api.yaml.
#
# price_api.py is also kept in workshop-python-interacting_with_kafka/sidecar/ for attendees
# to read — change both together. After changing it, rebuild with:
#   oc start-build electricity-price-api -n workspace-images
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn
COPY price_api.py .
EXPOSE 5000
CMD ["python", "price_api.py"]

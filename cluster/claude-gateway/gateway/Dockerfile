FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

RUN dnf install -y --nodocs nodejs && \
    dnf clean all

USER 1001

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py .

ENV SANDBOX_WORKDIR=/tmp/sandbox
ENV EXEC_TIMEOUT_SECONDS=30
ENV PORT=8080

EXPOSE 8080

CMD ["python", "gateway.py"]

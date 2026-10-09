FROM python:3.11-slim
WORKDIR /app
COPY relay.py /app/relay.py
ENV PYTHONUNBUFFERED=1 RELAY_PORT=8080
CMD ["python", "/app/relay.py"]

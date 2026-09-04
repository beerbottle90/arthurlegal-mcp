FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir "de-eli-mcp>=0.4"  && chmod +x start.sh  && python -c "import sqlite3, json, zipfile, urllib.request; print('stdlib ok')"

ENV MCP_TRANSPORT=http     MCP_HOST=0.0.0.0     MCP_PORT=8080     DE_ELI_URL=http://127.0.0.1:8790/mcp     INDEX_SOURCE=/app/baked

EXPOSE 8080
CMD ["./start.sh"]

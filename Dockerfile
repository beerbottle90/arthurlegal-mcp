FROM python:3.11-slim

WORKDIR /app
COPY . /app

# pypdf (pure Python) is the Turkish backend's one optional dependency: Rekabet,
# EPDK, SPK, BTK and Sigorta Tahkim publish decisions as PDF.
RUN pip install --no-cache-dir "de-eli-mcp>=0.4" "pypdf>=4.0"  && chmod +x start.sh  && python -c "import sqlite3, json, zipfile, urllib.request; print('stdlib ok')"

ENV MCP_TRANSPORT=http     MCP_HOST=0.0.0.0     MCP_PORT=8080     DE_ELI_URL=http://127.0.0.1:8790/mcp     INDEX_SOURCE=/app/baked

EXPOSE 8080
CMD ["./start.sh"]

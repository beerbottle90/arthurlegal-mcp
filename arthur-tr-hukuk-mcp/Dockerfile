FROM python:3.11-slim

WORKDIR /app
COPY . /app

# pypdf is pure Python; nothing compiles. The index is baked into the image
# (data/index.db) so a booting container is never healthy-but-empty.
RUN pip install --no-cache-dir -r requirements.txt \
 && chmod +x start.sh \
 && python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print('fts5 ok')"

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    INDEX_PATH=/app/data/index.db \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["./start.sh"]

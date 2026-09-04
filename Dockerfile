FROM python:3.11-slim

WORKDIR /app
COPY . /app

# The nine bundled jurisdiction servers are pure standard library. de-eli is the
# one exception -- it runs on FastMCP -- and is installed from PyPI so German law
# is not lost from a legal package. If the install is removed the aggregator
# still serves the other nine and says so in `status`.
RUN pip install --no-cache-dir "de-eli-mcp>=0.4" \
 && python -c "import sqlite3, json, zipfile, urllib.request; print('stdlib ok')" \
 && chmod +x start.sh

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    DE_ELI_URL=http://127.0.0.1:8790/mcp

EXPOSE 8080
CMD ["./start.sh"]

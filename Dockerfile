FROM python:3.11-slim

# Spaces runs as UID 1000. Create the user before any COPY so the bundled files
# -- and the SQLite indexes written next to them at runtime -- are writable.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

COPY --chown=user . $HOME/app

# The nine bundled jurisdiction servers are pure standard library. de-eli is the
# one exception -- it runs on FastMCP -- and is installed so German law is not
# lost. If this install fails the aggregator still serves the other nine and
# says so in `status`.
RUN pip install --no-cache-dir --user "de-eli-mcp>=0.4"  && python -c "import sqlite3, json, zipfile, urllib.request; print('stdlib ok')"  && chmod +x start.sh

ENV MCP_TRANSPORT=http     MCP_HOST=0.0.0.0     MCP_PORT=7860     DE_ELI_URL=http://127.0.0.1:8790/mcp

EXPOSE 7860
CMD ["./start.sh"]

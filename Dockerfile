FROM python:3.11-slim

WORKDIR /app
COPY . /app

# pypdf (pure Python) is the Turkish backend's one optional dependency: Rekabet,
# EPDK, SPK, BTK and Sigorta Tahkim publish decisions as PDF.
# numpy is optional too: with it the semantic scan in retrieval.py is ~6x faster
# (16,545 vectors: 1.3 s -> 0.2 s); without it the same ranking comes slower.
RUN pip install --no-cache-dir "de-eli-mcp==0.5.4" "pypdf>=4.0" "numpy>=2.0,<3"  && chmod +x start.sh  && python -c "import sqlite3, json, zipfile, urllib.request, numpy; print('stdlib ok, numpy', numpy.__version__)"

# One BLAS thread: the machines have one shared vCPU, and a matrix-vector product
# per chunk gains nothing from more.
ENV MCP_TRANSPORT=http     MCP_HOST=0.0.0.0     MCP_PORT=8080     DE_ELI_URL=http://127.0.0.1:8790/mcp     INDEX_SOURCE=/app/baked     OPENBLAS_NUM_THREADS=1

EXPOSE 8080
CMD ["./start.sh"]

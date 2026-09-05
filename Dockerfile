FROM python:3.11-slim

WORKDIR /app

# Install buildanchor from PyPI
RUN pip install --no-cache-dir buildanchor

# Run BuildAnchor MCP server over stdio
ENTRYPOINT ["buildanchor", "mcp", "--stdio", "--allow-root", "/app"]

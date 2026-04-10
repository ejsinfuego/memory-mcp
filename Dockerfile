FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy application code.
COPY server.py /app/server.py

# The MCP HTTP endpoint is exposed on /mcp at port 3000.
EXPOSE 3000

CMD ["python", "server.py"]

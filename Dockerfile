FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY server.py ./
COPY mock_core ./mock_core
RUN pip install --no-cache-dir .

CMD ["python", "-m", "whisper_mcp.server"]

FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml .
COPY app/ app/

RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "horde-oai"]

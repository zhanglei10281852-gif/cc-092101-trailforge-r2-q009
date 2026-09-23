FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAILFORGE_DATABASE_URL=sqlite:///./data/trailforge.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY trailforge ./trailforge
RUN python -m pip install --no-cache-dir .
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "trailforge.main:app", "--host", "0.0.0.0", "--port", "8000"]

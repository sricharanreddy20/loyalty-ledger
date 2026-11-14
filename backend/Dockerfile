# Build from the repository root (docker-compose sets context: .) so core/ is included.
# generator/ and db/schema.sql are included too, so the data can be loaded with Docker only:
#   docker compose run --rm backend python -m generator --scale starter --load
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY core/ core/
COPY backend/ backend/
COPY generator/ generator/
COPY db/schema.sql db/schema.sql

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

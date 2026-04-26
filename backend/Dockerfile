FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY sql ./sql
COPY alembic.ini .
COPY alembic ./alembic
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh 2>/dev/null || true \
    && chmod +x /app/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

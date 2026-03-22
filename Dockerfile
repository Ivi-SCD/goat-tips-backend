FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2 + scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 300 -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY models/ ./models/
COPY data/ ./data/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

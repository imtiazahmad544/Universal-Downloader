FROM python:3.12-slim

WORKDIR /srv/app

# Install dependencies first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code (app.main is provided by a later workstream)
COPY app/ ./app/
COPY alembic.ini ./
COPY migrations/ ./migrations/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

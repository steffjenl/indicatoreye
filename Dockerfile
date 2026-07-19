FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/config/config.json \
    STATE_PATH=/config/state.json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && if ! apt-get install -y --no-install-recommends libglib2.0-0; then \
        apt-get install -y --no-install-recommends libglib2.0-0t64; \
    fi \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- klient ---
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- API + statiske filer ---
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 RADAR_DATA_DIR=/data
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/radar ./radar
COPY --from=web /web/dist ./web
RUN useradd -r -u 10001 radar && mkdir -p /data && chown radar /data
USER radar
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "radar.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

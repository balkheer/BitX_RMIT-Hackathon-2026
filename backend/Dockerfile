# ConsentBridge API
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin cb
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
USER cb
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# --no-access-log: uvicorn's access log includes query strings; the app logs route templates only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8010 BRAND_DIR=/data/brand \
    FORWARDED_ALLOW_IPS='*'
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY pipeline ./pipeline
COPY db ./db
RUN mkdir -p /data/brand && useradd -r -u 10001 ledger && chown -R ledger /data
USER ledger
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/healthz').status==200 else 1)"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]

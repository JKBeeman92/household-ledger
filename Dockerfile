FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8010 BRAND_DIR=/data/brand \
    FORWARDED_ALLOW_IPS='*'
# The image listens on 0.0.0.0, so AUTH_DISABLED=true here also needs I_KNOW_AUTH_IS_OFF=1 (app/config.py)
ENV LEDGER_IN_CONTAINER=1
WORKDIR /srv
# Take the base image's pending Debian security updates: python:*-slim lags them, and the PR image scan fails on fixable HIGH/CRITICAL.
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# Then drop pip, and the copy of it ensurepip keeps, once it has installed the app: nothing in the image runs it, and its vendored
# msgpack, urllib3 and setuptools (pkg_resources) are what the PR image scan flags (#218, read from pip/_vendor/bom.cdx.json).
# Upgrading pip is not enough: 26.3 still vendors setuptools 70.3.0. To add a package, rebuild; don't install into a running container.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pip && rm -rf /usr/local/lib/python3*/ensurepip/_bundled
COPY app ./app
COPY pipeline ./pipeline
COPY db ./db
COPY examples/sample-household ./examples/sample-household
COPY version.txt ./
RUN mkdir -p /data/brand && useradd -r -u 10001 ledger && chown -R ledger /data
USER ledger
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/healthz').status==200 else 1)"
# X-Forwarded-* are applied inside the app (FORWARDED_ALLOW_IPS, app/main.py) and the app writes the access line itself, with the
# client and the proxy it came through (#111). uvicorn would otherwise do both first, by default, and hide the proxy's address.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers --no-access-log"]

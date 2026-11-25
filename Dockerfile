# GET.route Apple Maps: a small Python service (no browser), any CPU.
# Answers come from Apple's Maps Server API over HTTPS; the console is at /admin.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    APPLEMAP_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Runs as root so it can read the key file Container Manager mounts from the
# NAS (deploy/nas/secrets) and write the database to deploy/nas/data, whatever
# their owner on the NAS.
RUN mkdir -p /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --proxy-headers --forwarded-allow-ips '*'"]

# A small Python image: answers come from Apple's Maps Server API over HTTPS,
# so there is no browser in the container.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY apple_auth.py apple_maps.py config.py guard.py main.py ./

# Runs as root so it can read the key file Container Manager mounts from the
# NAS (deploy/nas/secrets), whatever its owner.
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]

# Microsoft's Playwright image: Python, Chromium and every library Chromium
# needs, for x86-64 (the DS224+'s Celeron J4125). Its version must match
# playwright in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY apple_maps.py browser_client.py config.py guard.py main.py ./

# Runs as root, like GET.route, so it can write to the debug folder that
# Container Manager mounts from the NAS (Chromium runs with --no-sandbox).
RUN mkdir -p /app/debug

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY skills ./skills
COPY mcp_servers ./mcp_servers

RUN groupadd -g 10002 ai \
 && useradd -u 10002 -g ai -m -s /usr/sbin/nologin ai \
 && mkdir -p /app/data/kb /app/data/uploads \
 && chown -R ai:ai /app

USER ai
EXPOSE 8086

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8086"]
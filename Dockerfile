# syntax=docker/dockerfile:1

ARG RUNTIME_IMAGE=registry.shawsx.com:8443/library/python-web-stack:latest

FROM node:24-alpine AS frontend-build

WORKDIR /build

COPY web/package.json web/package-lock.json ./

RUN npm config set registry https://registry.npmmirror.com \
    && npm ci

COPY web/ ./

ARG BASE_PATH=/quant-sentinel/

RUN ./node_modules/.bin/tsc -b \
    && ./node_modules/.bin/vite build --base="${BASE_PATH}"

FROM ${RUNTIME_IMAGE} AS runtime

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g' \
            -e 's@security.debian.org@mirrors.tuna.tsinghua.edu.cn@g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g' \
            -e 's@security.debian.org@mirrors.tuna.tsinghua.edu.cn@g' \
            /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
        logrotate \
        tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    ln -snf /usr/share/zoneinfo/"$TZ" /etc/localtime; \
    echo "$TZ" > /etc/timezone; \
    rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

COPY api/requirements.txt ./api/requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r ./api/requirements.txt

COPY api/ ./api/

RUN python -m pip install --no-cache-dir --no-deps ./api

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/supervisord.conf /etc/supervisord.conf
COPY deploy/logrotate-nginx.conf /etc/logrotate-nginx.conf
COPY deploy/entrypoint.sh /entrypoint.sh
COPY --from=frontend-build /build/dist /usr/share/nginx/html

RUN chmod 0755 /entrypoint.sh \
    && mkdir -p \
        /app/data \
        /app/logs/app \
        /app/logs/nginx \
        /app/logs/supervisord

EXPOSE 80

ENTRYPOINT ["/entrypoint.sh"]

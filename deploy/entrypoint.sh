#!/bin/sh
set -e

required_vars="${XQUANT_REQUIRED_ENV_VARS:-TZ,XQUANT_DB_PATH}"
missing_vars=""

for var_name in $(printf '%s' "$required_vars" | tr ',' ' '); do
    var_value=$(eval "printf '%s' \"\${$var_name-}\"")
    if [ -z "$var_value" ]; then
        missing_vars="$missing_vars $var_name"
    fi
done

if [ -n "$missing_vars" ]; then
    echo "启动失败：缺少必需环境变量：$missing_vars" >&2
    echo "请在 deploy/.env 或 Jenkins 环境变量中补充后重新部署。" >&2
    exit 1
fi

mkdir -p "$(dirname "$XQUANT_DB_PATH")" /app/logs/app /app/logs/nginx /app/logs/supervisord

exec supervisord -c /etc/supervisord.conf

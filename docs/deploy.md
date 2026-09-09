# 部署模板提示词

请为本项目生成适配以下环境的部署文件，风格与约定必须与模板保持一致。

## 固定环境（不可更改）
- Jenkins 使用声明式 pipeline，`agent any`，节点内置 Node 24：`NODE_HOME=/var/jenkins_home/tools/node-v24`。
- npm 使用国内镜像：`https://registry.npmmirror.com`。
- Docker 私仓：`registry.shawsx.com:8443`，镜像统一放在 `library/` 下。
- 运行时基础镜像：`registry.shawsx.com:8443/library/python-web-stack:latest`（python:3.12-slim，已内置 nginx、supervisor、Alloy）。
- 服务器时区固定 `Asia/Shanghai`；apt 使用清华镜像源。
- 容器内架构：nginx 监听 80，后端 uvicorn 监听 127.0.0.1:8000，supervisor 托管全部进程。
- 内网 DNS 通过 `extra_hosts` 映射：
  - `loki.shawsx.com -> 10.10.10.100`
  - `newapi.shawsx.com -> 10.10.10.100`
- 服务由 Jenkins 所在主机直接执行 `docker compose -f deploy/docker-compose.yaml up -d` 部署，`restart: unless-stopped`。

## 项目参数（按本项目实际情况替换）
- `PROJECT_NAME`：服务名，用于镜像名、容器名、compose service 名。
- `HOST_PORT`：宿主机对外端口（容器内固定 80）。
- `BACKEND_CMD`：后端启动命令，默认 `uvicorn main:app --host 0.0.0.0 --port 8000`。
- `FRONTEND_DIR`：前端目录，默认 `web`。
- `API_DIR`：后端目录，默认 `api`。
- `BASE_PATH`：前端路由 base，默认 `/PROJECT_NAME/`。
- 必需环境变量清单：按项目列出，写入 entrypoint 的启动前校验。

## 需要生成的文件
1. `Jenkinsfile`
   - stages：Checkout → Build Frontend → Build Docker Image → Push Image → Deploy。
   - 镜像 tag 使用 `date +%Y%m%d%H%M`，同时打时间戳 tag 和 `latest`。
   - 推送两个 tag 后，用 `IMAGE_TAG=<tag> docker compose -f deploy/docker-compose.yaml up -d` 部署，再执行 `docker image prune -f`。
   - 注册表凭据必须用 `withCredentials` 引用 Jenkins credential，不允许明文写在文件里。
   - post 中 success/failure 输出中文结果说明。

2. `Dockerfile`
   - 多阶段构建：前端构建阶段 + `python-web-stack` 运行时阶段。
   - 运行时阶段安装：`libpq-dev gcc logrotate tzdata`，切清华 apt 源，删除 nginx 默认站点。
   - 依赖安装与代码拷贝分层，充分利用构建缓存。
   - 拷贝 `deploy/` 下的 nginx、supervisord、logrotate、entrypoint 配置到镜像内固定路径。
   - `EXPOSE 80`，`ENTRYPOINT ["/entrypoint.sh"]`。

3. `deploy/docker-compose.yaml`
   - service 名、container_name、镜像名、宿主机端口按项目参数生成。
   - 环境变量一律使用 `${VAR:-default}` 占位，敏感信息（数据库/Redis/S3/JWT/管理员密码等）不硬编码，通过 `.env` 或 Jenkins 注入。
   - 包含 `extra_hosts` 内网域名映射和 `restart: unless-stopped`。

4. `deploy/nginx.conf`
   - SPA 部署：`BASE_PATH` 路径 alias 到 `/usr/share/nginx/html/`，`try_files` 回退到 `BASE_PATH/index.html`。
   - `index.html` 强制 `no-cache, no-store, must-revalidate`；带哈希的静态资源 `max-age=31536000, immutable`。
   - `/api` 反代到 `http://127.0.0.1:8000`，读写超时按项目需要配置（默认 1860s）。

5. `deploy/supervisord.conf`
   - `nodaemon=true`，管理 nginx、后端进程、logrotate（可选 alloy）。
   - 所有程序日志双写：终端 stdout/stderr + `/app/logs/supervisord/*.log`。
   - 日志目录：`/app/logs/app`、`/app/logs/supervisord`、`/app/logs/nginx`。

6. `deploy/logrotate-nginx.conf`
   - 路径 `/app/logs/nginx/*.log`，daily、rotate 7、compress、copytruncate，postrotate 执行 `nginx -s reopen`。

7. `deploy/entrypoint.sh`
   - `set -e`；启动前校验必需环境变量，缺失则逐项输出中文错误并 exit 1。
   - 校验通过后 `exec supervisord -c /etc/supervisord.conf`。

## 生成要求
- 所有文件必须可直接被 `docker build` 和 Jenkins pipeline 使用，不留 TODO 占位。
- 保持现有文件命名与目录结构不变。
- 敏感配置不硬编码；示例值必须明显标注为需要替换的默认值。
- 输出前自查：镜像 tag 逻辑、compose 端口映射、nginx base 路径、supervisor 进程命令四处与项目参数一致。
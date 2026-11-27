# Frontend: the UI (changelog_watcher/static) on unprivileged nginx, which proxies everything
# else (/api, /docs, /metrics...) to BACKEND_URL. Build from the repository root:
#   docker build -f frontend/Dockerfile -t changelog-watcher-frontend .
# Pinned digest (Dependabot updates it, see .github/dependabot.yml). Mainline: nginx fixes reach it
# first (stable 1.30.6 had fixed CVEs still open).
FROM nginxinc/nginx-unprivileged:1.31-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e
# The entrypoint renders /etc/nginx/templates/*.template into /etc/nginx/conf.d (envsubst),
# only for BACKEND_URL so nginx's own $variables stay as they are.
ENV BACKEND_URL=http://changelog-watcher:8080 \
    NGINX_ENVSUBST_FILTER=^BACKEND_URL$
# Alpine security fixes published after the base image.
USER root
RUN apk upgrade --no-cache && rm /etc/nginx/conf.d/default.conf
USER 101
COPY frontend/default.conf.template /etc/nginx/templates/
COPY changelog_watcher/static/index.html /usr/share/nginx/html/
COPY changelog_watcher/static /usr/share/nginx/html/static
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO /dev/null http://127.0.0.1:8080/frontend-healthz || exit 1

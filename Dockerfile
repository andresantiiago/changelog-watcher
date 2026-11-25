# Digest fixado (o Dependabot atualiza, ver .github/dependabot.yml): build reproduzível e base revisada.
FROM python:3.15.2-slim@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5 AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY changelog_watcher ./changelog_watcher
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.15.2-slim@sha256:63bb298d2b7b88f176f51b5189f54ce6535c997682ea70885d2132f5d473a8a5
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////data/changelog-watcher.db \
    PORT=8080
# Correções de segurança do Debian publicadas depois da base; o pip não é usado em runtime.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app /data
COPY --from=build /install /usr/local
USER 10001
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" || exit 1
ENTRYPOINT ["changelog-watcher"]
CMD ["serve"]

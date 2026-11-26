# Changelog Watcher

Acompanha os changelogs/releases de uma lista de aplicações, compara com a versão
que está rodando em cada ambiente e avisa o que precisa ser atualizado.

- **Fontes de releases**: GitHub Releases/Tags, GitLab Releases/Tags, repositórios
  Helm (`index.yaml`), Docker Hub, arquivos `CHANGELOG.md` ou qualquer **página web**
  que liste as versões.
- **Versão em uso** informada manualmente ou **descoberta pelo Prometheus**
  (imagem do Deployment/StatefulSet/DaemonSet via kube-state-metrics), inclusive
  agrupando **todos os workloads de um release Helm** como uma única aplicação.
- **Descoberta no cluster**: lista o que roda num namespace agrupado por release
  Helm (com os releases criados por operadores ligados ao release pai) e cria uma
  aplicação por imagem com um clique, apontando para o repositório ou site de releases.
- **Vários ambientes** (dev, homologação, produção…), cada um com seu Prometheus,
  e **comparação de versões entre ambientes**.
- Para cada aplicação/ambiente: **bugfix** mais recente da linha em uso
  (estou na 1.1.3 e existe a 1.1.7), **minor** e **major** mais recentes.
- **Dashboard web**: % do ambiente atualizado, quantas aplicações estão atrás por
  bugfix/minor/major, há quanto tempo, libyears e a evolução ao longo do tempo.
- **Fim de suporte (EOL)** do ciclo em uso, consultado no [endoflife.date](https://endoflife.date),
  com aviso quando falta pouco (90 dias por padrão).
- **Relatório semanal** enviado por webhook (Slack, Microsoft Teams, Discord,
  Google Chat ou JSON genérico).
- **Métricas Prometheus** em `/metrics`, **dashboard do Grafana** e **chart Helm**
  inclusos.

![Dashboard: % atualizado, pendências por bugfix/minor/major, atraso e evolução por ambiente](docs/dashboard.png)

## Telas

**Aplicações**: versão em uso em cada ambiente, a maior atualização pendente e a versão
mais recente, com filtros por status, ambiente, tag e namespace.

![Lista de aplicações com a versão e o status em cada ambiente](docs/aplicacoes.png)

**Detalhe da aplicação**: por ambiente, a versão em uso (manual ou detectada via
Prometheus, com a imagem), o bugfix/minor/major disponível, há quanto tempo está
atrasada e o changelog pendente; abaixo, todas as versões publicadas com as notas.

![Detalhe do argo-cd com um cartão por ambiente e as notas das versões](docs/aplicacao.png)

**Comparar ambientes**: o que está à frente ou atrás entre dois ambientes (ex.:
produção × desenvolvimento), com as versões entre eles, e a matriz de versões de
todos os ambientes.

![Comparação produção × desenvolvimento](docs/comparar.png)

**Novas versões**: releases publicadas nos últimos 7/14/30/90 dias que ainda não
estão em uso, com o tipo de atualização e a versão de cada ambiente.

![Novas versões dos últimos 7 dias](docs/novas-versoes.png)

**Relatórios**: o relatório semanal (resumo por ambiente, novas versões e aplicações
desatualizadas) com o histórico, a prévia e o envio aos webhooks.

![Relatório semanal gerado](docs/relatorio.png)

A tela **Descobrir no cluster** está em
[Releases Helm e descoberta no cluster](#releases-helm-e-descoberta-no-cluster). As
imagens usam dados de demonstração; veja [docs/screenshots](docs/screenshots/README.md)
para regerá-las.

## Como as versões são classificadas

Para cada implantação (aplicação × ambiente) o watcher pega todas as versões
publicadas (pre-releases ignoradas, a menos que habilitado) e calcula:

| Campo | Significado | Exemplo (em uso 1.1.3) |
|---|---|---|
| **bugfix** | maior versão com o mesmo `major.minor` | 1.1.7 |
| **minor** | maior versão com o mesmo `major` e `minor` maior | 1.3.1 |
| **major** | maior versão com `major` maior | 2.0.0 |
| **status** | o maior salto disponível: `up_to_date`, `patch`, `minor`, `major` ou `unknown` | `major` |
| **atrasada há** | dias desde a publicação da primeira versão mais nova que a em uso | 200 dias |
| **libyears** | anos entre a publicação da versão em uso e a da mais recente ([libyear](https://libyear.com)) | 1,1 |

Por ambiente, o dashboard, o relatório e o Grafana mostram a **média** de libyears por implantação
(só as que têm libyears calculado), para que ambientes com mais aplicações não pareçam mais antigos só
por terem mais itens. A soma continua exposta em `environment_libyears_total`.

Versões maiores muito mais antigas que outra menor são ignoradas (em todo lugar: status,
métricas, comparação entre ambientes e lista de versões): uma versão sai quando todas as versões
iguais ou maiores que ela foram publicadas mais de 2 anos antes de alguma versão menor. Ex.: o
CoreDNS tem a tag `11.0.0` de 2016, enquanto a linha atual é a 1.15 (2026); ela não aparece como
major. Linhas antigas ainda mantidas (PostgreSQL 13 com patches depois do 14.0) continuam valendo,
porque há versões maiores recentes.

O status é exclusivo (cada implantação conta uma vez, pelo maior salto), mas o
dashboard também mostra quantas implantações têm *algum* bugfix/minor/major
pendente — uma app em `major` pode ter também um bugfix na sua linha.

Tags como `v1.2.3`, `release-1.2.3`, `chart-1.2.3` e imagens como
`1.25.3-alpine` são normalizadas. Número único com zero à esquerda (`v011`, `007`) não é
versão: é contador de build (as tags de 2016 do CoreDNS) e seria lido como major 11. Use **padrão de tag** (regex) para filtrar
tags de monorepos e **esquema de versão `major.bugfix`** para projetos como o
PostgreSQL, em que `16.2 → 16.9` é bugfix.

## Fim de suporte (EOL)

A cada atualização o watcher consulta o [endoflife.date](https://endoflife.date) e avisa quando o
ciclo da versão em uso já perdeu suporte (**EOL atingido**) ou perde em até `EOL_WARNING_DAYS`
dias (**EOL próximo**, 90 por padrão ≈ 3 meses).

- **Produto**: campo *Produto no endoflife.date* da aplicação (`eol_product` no seed), o
  identificador usado na URL do site (`postgresql`, `kubernetes`, `redis`, `nodejs`…). Vazio, com
  `EOL_AUTO_MATCH=true`, procura o nome da aplicação e o nome do repositório/imagem/chart na lista
  de produtos (com alguns apelidos: `postgres` → `postgresql`, `node` → `nodejs`, `k8s` →
  `kubernetes`…). `none` desativa a verificação para a aplicação.
- **Ciclo**: o ciclo mais específico que é prefixo da versão em uso (`1.29.3` → `1.29`,
  PostgreSQL `13.4` → `13`). Versão sem ciclo correspondente fica sem informação de EOL.
- Fontes `helm_index` sem *Comparar pelo appVersion* não entram na busca automática: a versão do
  chart não é a do produto (informe o produto só se as versões coincidirem).
- Onde aparece: tabela "Fim de suporte (EOL)" no dashboard, selo **EOL próximo/atingido** na lista
  de aplicações (com filtro), linha "Fim de suporte" no detalhe, seção no relatório semanal e nas
  mensagens dos webhooks (que passam a contar como "algo desatualizado" para `only_outdated`),
  métricas `app_eol_*` / `environment_apps_eol`, painel no Grafana e alertas
  `ChangelogWatcherEndOfLifeSoon` / `ChangelogWatcherEndOfLifeReached` no PrometheusRule.

Com NetworkPolicies, o endoflife.date precisa do egress para a internet
(`networkPolicy.internet.enabled`); sem acesso, a aplicação continua funcionando e o erro aparece
no detalhe da aplicação.

## Detecção da versão via Prometheus

Requer [kube-state-metrics](https://github.com/kubernetes/kube-state-metrics),
que expõe `kube_pod_container_info{namespace, pod, container, image}`.

| Modo | Configuração | Consulta gerada |
|---|---|---|
| `workload` | tipo, namespace, nome, container (opcional) | `kube_pod_container_info{namespace="argocd", pod=~"argocd-server-[a-z0-9]{1,10}-[a-z0-9]{5}", container="server"}` |
| `helm` | namespace e nome do release; imagem (opcional) escolhe qual imagem define a versão | pods com `app.kubernetes.io/instance=<release>` (`kube_pod_labels`); sem esse label, pods `<release>-*` |
| `image` | repositório da imagem (ou regex) e namespace opcional | `kube_pod_container_info{image=~"(.*/)?quay\.io/jetstack/cert-manager-controller(:.*\|@.*)?"}` |
| `query` | PromQL livre; as séries precisam do label `image` (ou `version`) | `{namespace}` e `{name}` são substituídos |

A tag da imagem vira a versão (`quay.io/argoproj/argocd:v2.9.3` → `2.9.3`). Durante
um rollout vence a imagem com mais pods. Em cada ambiente é possível sobrescrever
namespace e nome do workload, e o **seletor extra** do ambiente (ex.
`cluster="prod"`) permite usar um Thanos/Mimir com vários clusters.

O botão **Testar detecção**, na página da aplicação, mostra a consulta e o
resultado sem salvar nada.

### Releases Helm e descoberta no cluster

Charts que criam vários Deployments (Argo CD: server, repo-server, controller,
redis…) viram **uma aplicação** com o modo `helm`: a versão é a imagem usada pela
maioria dos pods do release, e no empate a imagem que dá nome ao release (use
**Imagem** para fixar qual delas, ex. `quay.io/argoproj/argocd`, quando o release
roda imagens de terceiros ou sidecars).

A tela **Descobrir no cluster** consulta um namespace (ou o cluster todo) e mostra
os workloads agrupados por release:

- os releases vêm dos Secrets de armazenamento do Helm (`sh.helm.release.v1.<release>.v<rev>`,
  métrica `kube_secret_info`, exportada por padrão);
- cada pod entra no release pelo label `app.kubernetes.io/instance` ou, sem ele,
  pelo prefixo do nome. Para usar o label (e ver o `helm.sh/chart`), habilite no
  kube-state-metrics:
  `--metric-labels-allowlist=pods=[app.kubernetes.io/instance,helm.sh/chart]`;
- cada **imagem** diferente do grupo pode ser monitorada como uma aplicação própria
  (ex.: o StatefulSet do Prometheus do `kube-prometheus-stack` roda `prometheus`,
  `prometheus-config-reloader` e `thanos`): a sugestão fixa a imagem com **Imagem**
  (modo `helm`) ou **Container** (modo `workload`), e a tela mostra qual aplicação já
  monitora cada imagem. A imagem principal do grupo é a da maioria dos pods; no empate,
  a que dá nome ao release/workload (`kube-prometheus-stack-prometheus` → `prometheus`);
- grupos que não são um release Helm, mas pertencem a um, aparecem logo abaixo dele com
  **parte de &lt;release&gt;**: pods criados por um operador com o próprio label de
  instância (`kube-prometheus-stack-prometheus`, `…-alertmanager`) e cujo nome começa
  pelo release, ou pods com label `app.kubernetes.io/part-of`/`release` apontando para
  ele (inclua esses labels no allowlist acima para usá-los). Workloads de subcharts
  mostram o próprio `helm.sh/chart`.

![Descoberta do kube-prometheus-stack: uma linha por imagem e os releases do operator ligados ao release Helm](docs/descobrir.png)

**Monitorar** abre o formulário já preenchido; informe de onde vêm as releases:
repositório GitHub/GitLab, CHANGELOG, repositório Helm ou a URL de uma **página
web** (fonte `webpage`: toda versão encontrada no texto vira uma release; use o
**padrão de tag** para ignorar versões de outros softwares citadas na página e a
**URL do CHANGELOG** para trazer notas e datas).

Sem repositório informado, o formulário **sugere as fontes mais prováveis** (o link
**sugerir fonte**, automático ao vir da descoberta). Clique numa delas para preencher o
formulário. As sugestões vêm, nesta ordem, de:

1. label `org.opencontainers.image.source` da imagem, lido do registry com pull anônimo:
   o repositório oficial, quando a imagem o publica. Só registries públicos são consultados
   (Docker Hub, ghcr.io, quay.io, gcr.io, registry.k8s.io, public.ecr.aws, mcr.microsoft.com,
   registry.gitlab.com, docker.elastic.co), para a API não acessar hosts da rede interna;
2. imagem de registry interno (ex.: `registry.intranet/nginx:1.30.0`): procura a imagem pública
   da qual ela é mirror, com a mesma tag (ou digest), e usa o label dela ou a imagem do Docker
   Hub. Tenta, nesta ordem: o registry original quando ele está no caminho
   (`harbor.corp/proxy/quay.io/org/app` → `quay.io/org/app`), `ghcr.io` e Docker Hub com os
   dois últimos segmentos do caminho, e as imagens do Docker Hub com o mesmo nome que sejam
   oficiais ou tenham ao menos 100 mil pulls;
3. imagem no `ghcr.io` → repositório GitHub de mesmo nome;
4. [Artifact Hub](https://artifacthub.io): repositórios Helm com um chart de mesmo nome,
   comparando pelo `appVersion` (oficiais ou com ao menos 5 estrelas);
5. imagem no Docker Hub (fonte `dockerhub`);
6. busca no GitHub por repositórios com o mesmo nome e ao menos 50 estrelas, por estrelas (use
   `GITHUB_TOKEN` para não esbarrar no limite de 10 buscas/min sem autenticação).

Artifact Hub e GitHub são buscados pelo nome do chart (label `helm.sh/chart`), pelo nome da
imagem e pelo nome da aplicação, nesta ordem: o nome da imagem diz o que roda mesmo quando o
workload tem outro nome (`frontend` rodando `nginx`).

Cada consulta é independente e tem até 15 s: se uma falhar (sem internet, imagem privada,
limite da API), só aparecem menos sugestões, e a falha fica nos logs (WARNING). A API é
`GET /api/suggest-source?name=&image=&chart=`.

## Executando

### Local

```bash
pip install -e ".[dev]"
```

```bash
CONFIG_FILE=config.example.yaml changelog-watcher serve
```

Abra http://localhost:8080. A documentação da API fica em `/docs`.

Ou com Docker:

```bash
docker compose up --build
```

Comandos avulsos (úteis em CronJobs):

```bash
changelog-watcher refresh
```

```bash
changelog-watcher report --send
```

### Kubernetes (Helm)

```bash
helm install changelog-watcher deploy/helm/changelog-watcher -n changelog-watcher --create-namespace -f meus-valores.yaml
```

Exemplo de `meus-valores.yaml`:

```yaml
config:
  publicUrl: https://changelog-watcher.empresa.com
  reportCron: "0 9 * * mon"
  timezone: America/Sao_Paulo

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: changelog-watcher.empresa.com
      paths: [{path: /, pathType: Prefix}]

secrets:
  values:
    GITHUB_TOKEN: ghp_xxx              # evita o limite de 60 req/h da API do GitHub
    PROM_PROD_TOKEN: xxx
    SLACK_WEBHOOK_URL: https://hooks.slack.com/services/...

metrics:
  serviceMonitor: {enabled: true, labels: {release: kube-prometheus-stack}}
  prometheusRule: {enabled: true}

grafana:
  dashboard: {enabled: true}           # ConfigMap para o sidecar do Grafana

seed:
  environments:
    - name: desenvolvimento
      prometheus_url: http://prometheus-dev.monitoring:9090
    - name: producao
      prometheus_url: https://thanos.empresa.com
      prometheus_auth: bearer
      prometheus_token: ${PROM_PROD_TOKEN}
  projects:
    - name: argo-cd
      repository: argoproj/argo-cd
      detection_mode: workload
      workload_namespace: argocd
      workload_name: argocd-server
      deployments: {desenvolvimento: {}, producao: {}}
  webhooks:
    - name: slack
      kind: slack
      url: ${SLACK_WEBHOOK_URL}
```

O chart cria o workload (StatefulSet ou Deployment, ver abaixo), Service, Secret, ConfigMap
do seed e, opcionalmente, Ingress, ServiceMonitor, PrometheusRule, NetworkPolicies e o
ConfigMap do dashboard.

A UI/API exige Basic Auth por padrão (`auth.enabled: true`). Sem `auth.password` nem
`auth.existingSecret`, o chart gera uma senha aleatória no Secret (mantida nos upgrades); o
comando para lê-la aparece nas notas do `helm install`. Use TLS no Ingress (`ingress.tls`):
a senha vai em cada requisição.

#### NetworkPolicies

Para clusters que bloqueiam tráfego por padrão, há duas NetworkPolicies independentes
(ambas desligadas por padrão e ambas liberam DNS na porta 53):

| Flag | Libera |
|---|---|
| `networkPolicy.prometheus.enabled` | ingress do namespace do Prometheus (scrape do `/metrics`) e egress para ele (consultas, porta `9090` por padrão) |
| `networkPolicy.internet.enabled` | egress para a internet pública (`0.0.0.0/0` menos as redes privadas, metadata da nuvem `169.254.0.0/16` e loopback em `except`): fontes de releases, webhooks e Prometheus/Thanos externos |

```yaml
networkPolicy:
  prometheus:
    enabled: true
    namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: monitoring}}
    allowIngressFrom:                  # quem mais acessa a UI, ex.: o ingress controller
      - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ingress-nginx}}
  internet:
    enabled: true
```

> Com `networkPolicy.prometheus` ligada o pod só aceita conexões do Prometheus e de
> `allowIngressFrom`: sem incluir o ingress controller, a UI fica inacessível pelo Ingress.

O banco é sempre SQLite (não há suporte a banco externo) e o modo é escolhido em
`persistence.type`:

| `persistence.type` | Workload | Dados ao reiniciar o pod |
|---|---|---|
| `pvc` (padrão) | StatefulSet com PVC em `/data` (`1Gi`, StorageClass `gp3`) | **mantidos** |
| `inMemory` | Deployment sem volume | **perdidos**: o estado volta a ser só o do `seed`; o que foi feito pela UI, histórico e relatórios somem |

Use `inMemory` apenas para testes (é o que o `values-kind.yaml` usa). O PVC sobrevive a um
`helm uninstall`; o tamanho/StorageClass não muda depois de criado (o `volumeClaimTemplates`
do StatefulSet é imutável), então para crescer edite o PVC diretamente. Para usar um PVC já
existente, informe `persistence.existingClaim`.

> O agendador (atualização e relatório) e o SQLite rodam dentro do processo, por isso o
> chart sempre cria **1 réplica**.

### Cluster kind local (imagem local)

Constrói a imagem, carrega no nó do kind e instala com [`deploy/helm/values-kind.yaml`](deploy/helm/values-kind.yaml)
(Prometheus do próprio cluster, `pullPolicy: Never`):

```bash
docker build -t changelog-watcher:local .
kind load docker-image changelog-watcher:local --name lab   # nome do seu cluster kind
helm upgrade --install changelog-watcher deploy/helm/changelog-watcher \
  -n changelog-watcher --create-namespace -f deploy/helm/values-kind.yaml
kubectl -n changelog-watcher port-forward svc/changelog-watcher 8080:80   # http://localhost:8080
```

Após mudar o código: refaça o `docker build` e o `kind load`, e reinicie com
`kubectl -n changelog-watcher rollout restart deploy/changelog-watcher`.

## Configuração

Tudo pode ser gerenciado pela interface. O arquivo YAML de seed
([`config.example.yaml`](config.example.yaml)) é opcional e serve para GitOps:
por padrão ele só cria o que ainda não existe; com `SEED_OVERWRITE=true` ele
sobrescreve os campos que declara.

Segredos (tokens do Prometheus, senhas, URLs de webhook) podem ser gravados como
`${NOME_DA_VARIAVEL}` — o valor é lido do ambiente na hora do uso e nunca fica no
banco. Valores digitados diretamente são mascarados nas respostas da API. Regras:

- o campo inteiro deve ser o placeholder (`${SLACK_WEBHOOK_URL}`); um placeholder no meio de
  um texto (`https://host/${X}`) não é expandido, para não ser enviado a um host qualquer;
- as credenciais da própria aplicação (`AUTH_*`, `GITHUB_TOKEN`, `GITLAB_TOKEN`,
  `METRICS_TOKEN`, `DATABASE_URL`) nunca são expandidas;
- ao trocar a URL do Prometheus de um ambiente, o token/senha mascarado precisa ser informado de novo.

Requisições a URLs configuradas (fontes, Prometheus, webhooks, inclusive redirects) são
recusadas quando o host resolve para loopback ou link-local (metadata da nuvem,
`169.254.169.254`). Redes privadas continuam permitidas.

| Variável | Padrão | Descrição |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/changelog-watcher.db` | banco SQLite (único suportado); `sqlite:///:memory:` = em memória, dados perdidos ao reiniciar |
| `CONFIG_FILE` | — | YAML de seed |
| `SEED_OVERWRITE` | `false` | seed sobrescreve alterações feitas pela UI |
| `GITHUB_TOKEN` / `GITHUB_API_URL` | — / `https://api.github.com` | token e API (GitHub Enterprise); o token só é enviado a essa API (e a `raw.githubusercontent.com`), nunca ao `source_url` de uma aplicação |
| `GITLAB_TOKEN` / `GITLAB_URL` | — / `https://gitlab.com` | token e URL do GitLab; o token só é enviado a essa URL |
| `REFRESH_INTERVAL_MINUTES` | `360` | intervalo da busca automática (0 desativa); padrão, editável na página Configuração |
| `REFRESH_ON_STARTUP` | `true` | busca ao iniciar |
| `REPORT_CRON` | `0 9 * * mon` | cron do relatório semanal; padrão, editável na página Configuração |
| `REPORT_DAYS` | `7` | janela de "novas versões" do relatório |
| `REPORT_SEND_ENABLED` | `true` | envia o relatório agendado aos webhooks |
| `TZ` | `UTC` | fuso do agendador |
| `PUBLIC_URL` | — | link para a UI incluído no relatório |
| `MAX_RELEASE_PAGES` | `3` | páginas de 100 releases/tags buscadas por fonte |
| `EOL_ENABLED` | `true` | consulta o fim de suporte (EOL) no endoflife.date a cada atualização |
| `EOL_API_URL` | `https://endoflife.date/api` | API do endoflife.date (ou um espelho) |
| `EOL_WARNING_DAYS` | `90` | avisa "EOL próximo" quando faltam até N dias |
| `EOL_AUTO_MATCH` | `true` | sem produto informado, procura pelo nome/repositório da aplicação |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | — | basic auth na UI/API, obrigatória (`/healthz` e `/readyz` ficam abertos); sem elas a aplicação não sobe |
| `AUTH_DISABLED` | `false` | `true` roda sem autenticação (só para testes locais) |
| `METRICS_TOKEN` | — | bearer token exigido em `/metrics`; vazio = aberto |
| `SCHEDULER_ENABLED` | `true` | desativa o agendador interno |
| `METRICS_CACHE_SECONDS` | `30` | cache das métricas calculadas |

### Criando os tokens

Todos são opcionais, mas recomendados. Nunca commite o valor: passe por variável de
ambiente (ou Secret do Kubernetes) e, nos campos da UI/seed, referencie com `${NOME}`.

**`GITHUB_TOKEN`** — evita o limite de 60 requisições/hora da API e permite ler repositórios privados.

1. GitHub → *Settings* → *Developer settings* → *Personal access tokens* → *Fine-grained tokens* → *Generate new token*.
2. Defina uma validade e, em *Repository access*, escolha *Public repositories (read-only)*. Para
   repositórios privados, selecione-os e conceda a permissão *Contents: Read-only*.
3. Copie o token (`github_pat_…`) e exporte `GITHUB_TOKEN`. Um token *classic* sem nenhum escopo
   também funciona para repositórios públicos (para privados, use o escopo `repo`).
4. GitHub Enterprise: gere o token na sua instância e configure também `GITHUB_API_URL` (o token só
   vai para essa URL; o `source_url` de uma aplicação nunca recebe o token).

**`GITLAB_TOKEN`** — GitLab → *Preferences* → *Access Tokens* (ou *Settings → Access Tokens* de um
projeto/grupo) com o escopo `read_api`. Para instalações próprias, configure `GITLAB_URL`.

**Token do Prometheus** (campo *token* de um ambiente com autenticação `bearer`) — só é necessário se o
Prometheus/Thanos estiver atrás de autenticação. Use um token de leitura da sua infraestrutura, por exemplo:

- Prometheus exposto por um proxy com ServiceAccount (OpenShift, oauth2-proxy):
  `kubectl create token <serviceaccount> -n <namespace> --duration=8760h`, com permissão apenas de leitura/consulta.
- Serviços gerenciados (Grafana Cloud, Thanos/Mimir com gateway): o API token de leitura do próprio serviço.

Um Prometheus sem autenticação (ex.: kube-prometheus-stack acessado dentro do cluster) usa `none` e não precisa de token.

## Webhooks

| Tipo | Formato enviado |
|---|---|
| `slack` | `{"text": …}` com mrkdwn |
| `teams` | Adaptive Card (Workflows / Power Automate) |
| `discord` | `{"content": …}`, quebrado em mensagens de até 2000 caracteres |
| `google_chat` | `{"text": …}` |
| `generic` | `{"event": "changelog_watcher.report", "markdown": …, "report": {…}}` com todos os dados |

Cada webhook pode ser limitado a alguns ambientes e configurado para só enviar
quando houver aplicações desatualizadas. O relatório contém: resumo por
ambiente, novas versões da semana (marcadas pelo impacto em relação ao que está
implantado), aplicações desatualizadas com bugfix/minor/major disponíveis,
aplicações sem versão identificada e diferenças entre ambientes. Relatórios
ficam salvos e podem ser baixados em Markdown.

## Métricas

Todas com prefixo `changelog_watcher_`, calculadas a partir do banco a cada scrape:

| Métrica | Labels | Descrição |
|---|---|---|
| `app_info` | project, environment, current_version, latest_version, latest_patch, latest_minor, latest_major, status, version_source | sempre 1 |
| `app_update_level` | project, environment | -1 desconhecido, 0 atualizado, 1 bugfix, 2 minor, 3 major |
| `app_update_available` | project, environment, type | 1 se há atualização do tipo |
| `app_versions_behind` | project, environment, type (`patch`/`minor`/`major`/`total`) | versões atrás |
| `app_outdated_days` | project, environment | dias desde a primeira versão mais nova |
| `app_libyears` | project, environment | libyears |
| `app_detection_error` | project, environment | 1 se a detecção via Prometheus falhou |
| `environment_apps` | environment, status | implantações por status |
| `environment_apps_pending` | environment, type | implantações com atualização do tipo (não exclusivo) |
| `environment_up_to_date_ratio` | environment | fração atualizada |
| `environment_apps_total` | environment | total de implantações |
| `environment_libyears_total` | environment | soma de libyears |
| `environment_libyears_avg` | environment | média de libyears por implantação (só implantações com libyears calculado) |
| `app_eol_level` | project, environment, product, cycle | 0 com suporte, 1 EOL próximo, 2 EOL atingido |
| `app_eol_days_left` | project, environment, product, cycle | dias até o fim de suporte (negativo: já passou) |
| `environment_apps_eol` | environment, status (`soon`/`eol`) | implantações com EOL próximo/atingido |
| `environment_outdated_days_avg` / `_max` | environment | atraso médio/máximo |
| `environment_versions_behind_total` | environment | soma de versões atrás |
| `environment_apps_by_age` | environment, bucket | desatualizadas por faixa de atraso |
| `environment_pair_different_apps` | source_environment, target_environment | apps com versões diferentes entre dois ambientes |
| `project_version_mismatch` / `project_distinct_versions` | project | divergência entre ambientes |
| `release_latest_timestamp_seconds` | project, version | publicação da versão mais recente |
| `releases_last_7d` | project | versões publicadas na semana |
| `project_source_error` | project, source_type | 1 se a última coleta falhou |
| `last_refresh_timestamp_seconds` / `last_refresh_success` | — | saúde da coleta |
| `refresh_duration_seconds`, `refresh_runs_total`, `source_fetches_total`, `prometheus_detections_total` | — | contadores/histograma do processo |

### Grafana

O dashboard está em
[`deploy/helm/changelog-watcher/dashboards/changelog-watcher.json`](deploy/helm/changelog-watcher/dashboards/changelog-watcher.json)
(importe pela UI ou use `grafana.dashboard.enabled` no Helm). Ele tem filtros de
ambiente e aplicação e mostra: % atualizado, pendências de bugfix/minor/major,
libyears, status por ambiente, faixas de atraso, evolução no tempo, tabela de
versões com as atualizações disponíveis, matriz de versões entre ambientes,
divergências entre pares de ambientes, histórico de status por aplicação, novas
versões e falhas de coleta.

O JSON é gerado por [`deploy/grafana/build_dashboard.py`](deploy/grafana/build_dashboard.py);
edite o script e rode-o para atualizar o JSON.

O `PrometheusRule` opcional traz alertas para versão major pendente há mais de N
dias, falha de detecção e coleta parada.

## Arquitetura

```
changelog_watcher/
  sources/      fontes de releases (github, gitlab, helm, dockerhub, changelog, página web)
  prometheus.py detecção da versão em uso via kube_pod_container_info
  versioning.py parsing de versões e classificação bugfix/minor/major
  analysis.py   status por implantação, resumos, comparação entre ambientes
  refresh.py    pipeline: detecção → releases → snapshot diário
  report.py     relatório (dados + Markdown)
  webhooks.py   formatos Slack/Teams/Discord/Google Chat/genérico
  metrics.py    collector Prometheus
  api.py        API REST (FastAPI)
  static/       frontend (HTML/JS/CSS, sem build)
deploy/
  helm/changelog-watcher/   chart Helm
  grafana/                  dashboard do Grafana
```

Um snapshot diário por ambiente alimenta os gráficos de evolução da UI; no
Grafana o histórico vem do próprio Prometheus.

## Limitações conhecidas

- **Docker Hub** não informa a data de publicação original da tag, só a do último
  push — imagens reconstruídas (ex.: correções de CVE) podem ter datas mais
  recentes. Sem `tag_pattern`, só tags de versão "puras" são consideradas
  (variações como `-alpine` são ignoradas; a versão em uso `1.25.3-alpine` vira
  `1.25.3`).
- A API do GitHub sem token permite 60 requisições/hora — configure `GITHUB_TOKEN`.
- Fontes paginadas (`MAX_RELEASE_PAGES`) só olham as N×100 versões mais recentes.
  Versões em uso muito antigas podem ficar sem data (libyears não calculado).
- As migrações de banco só adicionam colunas novas; não há remoção/renomeação.

## Desenvolvimento

```bash
pytest -q
```

```bash
python deploy/grafana/build_dashboard.py
```

```bash
helm lint deploy/helm/changelog-watcher
```

### Versionamento e releases

A imagem da aplicação e o chart Helm têm **versões separadas**, cada uma com seu padrão de tag:

| Componente | Muda quando alterar | Tag e release | Arquivos atualizados |
|---|---|---|---|
| Imagem | `changelog_watcher/`, `Dockerfile`, `.dockerignore`, `pyproject.toml` | `vX.Y.Z` + imagem no GHCR | `version` do `pyproject.toml`, `appVersion` do `Chart.yaml` |
| Chart Helm | `deploy/helm/changelog-watcher/` | `chart-vX.Y.Z` | `version` do `Chart.yaml` |

Uma nova imagem muda o `appVersion` (a imagem padrão do chart), então também gera uma versão
do chart: patch, se o chart em si não mudou. Documentação, testes e CI **não geram versão**:
as mudanças de README e `docs/` esperam a próxima release de imagem ou chart e entram nas
notas dela. As tags `vX.Y.Z` anteriores à separação valiam para os dois; o chart parte
delas até existir a primeira `chart-vX.Y.Z`.

Versões usam `vX.Y.Z` (com `v`) no `Chart.yaml`, nas tags, na imagem e na release. O
`pyproject.toml` segue o padrão do pip, sem `v` (`X.Y.Z`, rc como `X.Y.ZrcN`).

Todo PR roda o workflow `ci` (testes, dashboard e Helm); o resultado de cada teste aparece no
resumo da execução no GitHub Actions e num comentário no PR (atualizado a cada push). Cada merge na `main` roda o workflow `release`, que
primeiro executa o `ci` e só então, para cada componente que mudou desde a sua última tag:

1. o Copilot CLI lê as mudanças do componente (commits, arquivos e diff, mais README e `docs/`)
   e responde `major`, `minor` ou `patch` mais até 3 tópicos descrevendo o que mudou;
2. a versão vem do arquivo do componente (`pyproject.toml`/`appVersion` para a imagem,
   `version` do `Chart.yaml` para o chart) ou, só na imagem, de uma tag `vX.Y.Z-rcN`, quando
   for maior que a última tag final (vale a maior). O sufixo `-rcN` é sempre removido: o merge
   na `main` é a versão final (`v0.2.0-rc3` → `v0.2.0`). Senão a última tag é incrementada
   pela classificação do Copilot (sem resposta válida, pelas mensagens dos commits do
   componente: `BREAKING CHANGE`/`tipo!:`/`[major]` → major, `feat:`/`[minor]` → minor, o resto → patch);
3. se o `pyproject.toml` ou o `Chart.yaml` ficaram desatualizados, o workflow faz o commit
   corrigindo (`chore(release): vX.Y.Z, chart-vX.Y.Z [skip ci]`);
4. cria as tags; para a imagem publica `ghcr.io/andresantiiago/changelog-watcher` com as tags
   `vX.Y.Z`, `vX.Y`, `latest` e `sha-<commit>`; e cria as releases no GitHub com a descrição
   do Copilot (ou os assuntos dos commits do componente desde a última tag dele, se o
   Copilot falhar). A release do chart não é marcada como
   "Latest".

Durante o desenvolvimento, criar uma tag `vX.Y.Z-rcN` (ex.: `git tag v0.2.0-rc1 && git push origin v0.2.0-rc1`)
publica a imagem `vX.Y.Z-rcN` (sem mexer em `latest`) e uma pre-release no GitHub, sem
commit na `main`. No merge seguinte a release sai como `vX.Y.Z`.

O chart usa `appVersion` como tag da imagem, então cada versão do chart aponta para a
imagem mais recente na data da release dele. O workflow usa o secret `GH_TOKEN`: um token fine-grained com
**Contents** (escrita, para o commit e as tags) e **Copilot Requests**.

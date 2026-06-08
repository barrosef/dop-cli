# ADR-16 — Mudanças no workspace Optum (aplicar fora do dop-cli)

Estas mudanças vivem no workspace `/opt/wks/csptech/optum`, **não** no `dop-cli`.
O `dop` (a partir desta versão) já lê `test_root`/`aaa_root`/`it_root` do config e
expõe `dop aaa`/`dop it`. Falta apenas habilitar o lado do workspace.

> Resumo das decisões (ver `docs/superpowers/specs/2026-06-08-dop-aaa-it-test-root-design.md`):
> Maven roda **no host** (aaa e it); reports unificados em `<test_root>/reports`
> (Optum: `test/e2e/reports`); projects Allure: `e2e-<suite>`, `aaa-<repo>`, `it-<repo>`.

## 1. Migrar `e2e/` → `test/e2e/`

```bash
cd /opt/wks/csptech/optum
mkdir -p test
mv e2e test/e2e
```

(Nada é git; preserva `reports/` e o histórico Allure. Código de teste não muda —
conftest/specs usam paths relativos.)

## 2. `docker-compose.yml` — ajustar mounts (manter caminho interno `/e2e`)

O caminho **dentro** do container continua `/e2e` — só o lado host muda:

- `playwright-env`:  `./e2e:/e2e`                          → `./test/e2e:/e2e`
- `allure`:          `./e2e/reports:/app/projects`         → `./test/e2e/reports:/app/projects`
- `allure-ui`:       `./e2e/reports:/usr/share/nginx/html:ro` → `./test/e2e/reports:/usr/share/nginx/html:ro`

(Os literais `/e2e/...` dentro dos comandos do `dop` permanecem válidos por causa
deste mount — não há nada a mudar no `dop` para isso.)

## 3. `~/.config/dop/config.toml` — `[workspaces.optum]`

```toml
test_root = "test/e2e"
# aaa_root / it_root usam os defaults "test/aaa" / "test/it" — só sobrescreva se mudar o layout.
```

## 4. Renomear reports e2e existentes → `e2e-<suite>`

O `dop e2e` agora gera `reports/e2e-<suite>` (antes `reports/<suite>`). Para
preservar os reports **já gerados**, renomeie os diretórios de report (NÃO os de
staging `<jira>/<suite>/run-N`, que continuam por suíte):

```bash
cd /opt/wks/csptech/optum/test/e2e/reports
for d in optum-support-fe providers-front-end; do
  [ -d "$d" ] && [ ! -d "e2e-$d" ] && mv "$d" "e2e-$d"
done
```

(Atualizar também o `index.html`/links do `:5252` para o naming
`e2e-<suite>` / `aaa-<repo>` / `it-<repo>`.)

## 5. Scaffolding dos poms piloto (ADR-16)

Fora do escopo do `dop-cli` — são projetos Maven no workspace:

- `test/aaa/lifesupport-api/pom.xml` (**SUOPT-3184**): parent = pom do app via
  `relativePath ../../../repos/lifesupport-api/pom.xml`; `build-helper-maven-plugin`
  adiciona `../../../repos/lifesupport-api/src/main/java` como source; surefire +
  `allure-junit5` + `aspectjweaver`; testes em `src/test/java`. Resultados em
  `target/allure-results` (o `dop aaa` agrega e publica como `aaa-lifesupport-api`).
- `test/it/optum-support-be/pom.xml` (**SUOPT-3188**): mesmo esquema parent+build-helper
  + `maven-failsafe-plugin` (perfil `it`, rodado por `mvn -Pit verify`), Testcontainers
  (MySQL/Mongo efêmeros por run), schema via Flyway no startup, datasource via
  `@DynamicPropertySource`. Publicado como `it-optum-support-be`.

**Pré-requisitos do host** (decisão "Maven no host"): JDK 17 + Maven (ou `mvnw` no
projeto) disponíveis; Docker disponível para o Testcontainers do `dop it`.

## 6. Docs / memória

- `CLAUDE.md` / `agent-rules.md`: adicionar `dop aaa`/`dop it` na tabela de
  ferramentas; paths `test/`; gate das três camadas (verde local pré-PR).
- Atualizar refs `e2e/...` → `test/e2e/...`.

## Validação ponta-a-ponta (após aplicar 1–5)

```bash
dop aaa lifesupport-api          # roda mvn test, publica aaa-lifesupport-api
dop it  optum-support-be         # roda mvn -Pit verify (Testcontainers), publica it-optum-support-be
dop aaa all                      # itera todos os projetos em test/aaa/
dop e2e <suite>                  # continua funcional, agora a partir de test/e2e/
```

Confira os três tipos de project em `http://localhost:5252/`.

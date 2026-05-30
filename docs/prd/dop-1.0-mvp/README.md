# PRD Base — DOP 1.0 (MVP)

> **Documento vivo.** Captura todos os requisitos do DOP 1.0 a partir da visão do
> Dev. Servirá de fonte para o fatiamento em *feature PRDs* e para a divisão de
> responsabilidades Dev × Claude.
>
> - **Status:** Rascunho para revisão
> - **Data:** 2026-05-30
> - **Base atual:** dop 0.5.0 (CLI multi-workspace, multi-plataforma, runtime data-driven)
> - **Não-objetivo deste documento:** decidir arquitetura/stack/implementação (isso é
>   responsabilidade do Claude, ver [§10](#10-decisões-técnicas-em-aberto-a-cargo-do-claude)).
>   Aqui o foco é **o quê** e **para quem**, não **como**.

## Índice

1. [Visão e proposta de valor](#1-visão-e-proposta-de-valor)
2. [Objetivos e não-objetivos do 1.0](#2-objetivos-e-não-objetivos-do-10)
3. [Atores, personas e divisão de responsabilidades](#3-atores-personas-e-divisão-de-responsabilidades)
4. [Arquitetura de alto nível (descritiva)](#4-arquitetura-de-alto-nível-descritiva)
5. [Conceitos e estados](#5-conceitos-e-estados)
6. [Capacidades / Épicos e requisitos](#6-capacidades--épicos-e-requisitos)
7. [Inferência (reduzir trabalho e erro do Dev)](#7-inferência-reduzir-trabalho-e-erro-do-dev)
8. [Requisitos não-funcionais](#8-requisitos-não-funcionais)
9. [Mapa de features e divisão de responsabilidades](#9-mapa-de-features-e-divisão-de-responsabilidades)
10. [Decisões técnicas em aberto (a cargo do Claude)](#10-decisões-técnicas-em-aberto-a-cargo-do-claude)
11. [Questões de produto em aberto (a validar com o Dev)](#11-questões-de-produto-em-aberto-a-validar-com-o-dev)
12. [Fora de escopo do 1.0](#12-fora-de-escopo-do-10)
13. [Glossário](#13-glossário)

---

## 1. Visão e proposta de valor

O DOP evolui de uma **CLI** para uma **ferramenta auxiliar ao desenvolvimento de
software IA-first**, composta por três componentes — **CLI, API e Frontend** — em
que **Dev** e **Claude** colaboram para conduzir demandas do início ao fim
(configuração do ambiente → desenvolvimento → PR → entrega).

A CLI continua fazendo tudo o que faz hoje (git/PR multi-plataforma, runtime
docker-compose, e2e). A novidade é **expor essas capacidades via API** e oferecer um
**Frontend** onde o Dev trabalha conversando com o Claude, com visibilidade rica do
estado de cada demanda.

**Frase-âncora:** *"Um cockpit onde Dev e Claude desenvolvem juntos — da
configuração da workspace ao PR entregue — com o Claude operando o DOP por baixo e o
Dev acompanhando e guiando por cima."*

## 2. Objetivos e não-objetivos do 1.0

### Objetivos
- **O1.** Três componentes integrados: **CLI** (motor), **API** (orquestração HTTP),
  **Frontend** (UI do Dev).
- **O2.** **Toda ação que a CLI faz hoje** é acionável via API.
- **O3.** **Workspaces** configuráveis por **wizard** (multi-etapa, salvável por
  etapa), com teste de conexões, inferência e **chat de configuração** com o Claude.
- **O4.** **Fluxo de desenvolvimento** guiado por demanda: listar tasks do dev,
  trabalhar em chat com o Claude, acompanhar status até a entrega.
- **O5.** **Task manager plugável** (Jira no 1.0), com a mesma filosofia de interface
  do runtime/git providers.
- **O6.** **Geração assistida** (pelo Claude) de artefatos da workspace (Dockerfiles,
  docker-compose, regras/contexto).

### Não-objetivos do 1.0
- Não substituir pipelines de CI/CD remotos.
- Não fazer merge/aprovação automática de PR (decisão humana permanece).
- Não suportar webhooks (detecção de merge é por **polling** no 1.0).
- Multi-task-manager / multi-git-provider **simultâneos** além de Jira + Azure DevOps
  (a *plugabilidade* é requisito; as *implementações extras* não).

## 3. Atores, personas e divisão de responsabilidades

### Atores
- **Dev (humano):** configura workspaces, escolhe demandas, conversa com o Claude,
  aprova/decide, acompanha o estado. Pode ocasionalmente usar a **CLI** direto.
- **Claude (agente):** opera o DOP (git/PR/runtime/e2e), conversa com o Dev, faz
  perguntas para entender o projeto, gera artefatos e conduz a demanda.

### Cadeia de interação
```
Dev ──▶ Frontend ───────────────▶ API-DOP ──▶ workspace + ferramentas
                                   ▲   │          (git / azure / jira / docker, estado)
                                   │   └──▶ Claude (agente) ◀──▶ Dev  (chat)
                                   │                 │
                                   └──── CLI ◀───────┘
                          (Claude roda `dop ...`; a CLI só repassa para a API)
```
- A **API é o núcleo/motor**: contém a lógica de negócio (que hoje vive na CLI),
  **acessa a workspace e as ferramentas** (git/azure/jira/docker) e **detém o estado**.
  É a **única fonte de verdade**.
- Há **duas portas de entrada para a API**: o **Frontend** (usado pelo Dev) e a
  **CLI** (usada pelo Claude).
- A **CLI virou cliente fino da API**: o Claude continua rodando `dop ...` como hoje,
  mas a CLI **não acessa a workspace diretamente** — ela traduz comandos em chamadas
  à API. Faz tudo o que faz hoje, porém **via API**.
- O **Claude** é hospedado/dirigido pela API, **conversa com o Dev** (chat) e
  **opera o DOP** acionando a CLI (→ API).

### Divisão de responsabilidades (deste 1.0)
| Domínio | Responsável | Observação |
|---|---|---|
| Requisitos de produto, expectativa de usuário | **Dev** | Fonte da verdade do "o quê". |
| UX/UI, fluxos de tela, interação do Dev | **Dev** | Wizard, dashboards, chat UI. |
| Requisitos da CLI usada pelo Dev | **Dev** | Ergonomia da CLI ocasional. |
| Arquitetura, stack, integrações, implementação | **Claude** | Liberdade para escolher/testar/implementar. |
| Integração com Claude (mecanismo), API↔CLI, persistência, segurança técnica | **Claude** | Decisões em [§10](#10-decisões-técnicas-em-aberto-a-cargo-do-claude). |

## 4. Arquitetura de alto nível (descritiva)

> Descrição **conceitual** dos componentes e do fluxo de dados. As escolhas técnicas
> (stack, in-process × subprocess, persistência, mecanismo de chat com o Claude)
> estão em aberto em [§10](#10-decisões-técnicas-em-aberto-a-cargo-do-claude).

- **Componente 1 — API (núcleo/motor):** contém a lógica de negócio (hoje na CLI),
  **acessa a workspace e as ferramentas** (git/azure/jira/docker), **mantém o estado**
  de workspaces/demandas, hospeda/dirige o **Claude** e faz o **polling de PRs**.
  **Única fonte de verdade.**
- **Componente 2 — CLI (cliente fino da API):** porta de entrada usada pelo **Claude**
  (continua rodando `dop ...` como hoje). **Não acessa a workspace diretamente** —
  repassa tudo para a API. Mantida ergonômica para uso ocasional do Dev.
- **Componente 3 — Frontend:** SPA onde o Dev navega (tela inicial, wizard de
  workspace, menu de desenvolvimento, lista de tasks, painel da demanda, chat). Porta
  de entrada usada pelo **Dev**.
- **Claude:** acionado pela API como colaborador/operador; **conversa com o Dev** e
  **opera o DOP via CLI → API**, tanto na configuração quanto no desenvolvimento.

> **Consequência estrutural (a cargo do Claude/impl):** a lógica que hoje reside no
> pacote da CLI (`git/`, `platform/`, `runtime/`, `core/state`) **migra para um núcleo
> consumido pela API**; a CLI é reescrita como cliente HTTP fino. É a maior mudança de
> estrutura do projeto no 1.0.

**Princípios herdados (mantidos como requisito):**
- Estado auditável (hoje `.state.json`); operações idempotentes; `--dry-run`;
  **redação de segredos em toda saída**; multi-workspace; providers plugáveis
  (git/runtime/**task manager**).

## 5. Conceitos e estados

### 5.1 Workspace — estados
`draft` → `ativa` → `inativa` → `deletada`
- **draft:** criada via wizard, possivelmente incompleta (salvável etapa a etapa).
- **ativa:** configurada e utilizável para desenvolvimento.
- **inativa:** desativada temporariamente (não aparece para trabalho, mas preservada).
- **deletada:** removida logicamente.

### 5.2 Demanda — status do DOP (independente do status no task manager)
| Status DOP | Significado |
|---|---|
| `new` | A demanda ainda não começou no DOP. |
| `doing` | Claude e Dev estão trabalhando nela. |
| `done` | PR feito; Claude e Dev consideraram terminada. |
| `delivered` | PR mergeado **e** pipeline rodou no ambiente de dev. |

- O card exibe **dois status**: o do **task manager** (Jira) e o do **DOP**.
- A transição `done → delivered` é detectada por **polling periódico** (intervalo
  configurável), via API/az-cli/MCP (o que for mais simples) — **sem webhook** no 1.0.

### 5.3 Estrutura de pastas da workspace
```
<root>/
├── docs/
│   ├── RFC/
│   ├── ADR/
│   └── prompts/
├── repos/
│   ├── <repo1>/
│   ├── <repo2>/
│   └── ...
└── runtime/
    ├── docker/            # Dockerfiles de dev — gerados pelo Claude
    └── docker-compose/    # docker-compose.yaml — gerado pelo Claude na config
```

## 6. Capacidades / Épicos e requisitos

> Cada épico vira (na próxima fase) um *feature PRD* próprio. IDs de requisito no
> formato `R<épico>.<n>`.

### E1 — Workspaces (configuração via wizard)

**User story principal**
> Como Dev, quero uma tela inicial com opção de **criar/configurar várias
> workspaces**, configurando repositórios remotos, provider git, fluxo de branches,
> task manager e runtime — testando conexões na criação — para preparar o ambiente
> de trabalho IA-first com mínimo esforço e erro.

**Requisitos**
- **R1.1** Tela inicial lista workspaces existentes e permite **criar** nova.
- **R1.2** Wizard **multi-etapa**, **salvável etapa a etapa**; o Dev pode **editar
  qualquer workspace em qualquer etapa** (completa ou incompleta).
- **R1.3** Configurar **repositórios remotos** git com protocolo **http/https/ssh**;
  para cada tipo, informar as **credenciais** apropriadas (login/token; chave SSH).
- **R1.4** Escolher o **provider git** (no 1.0: **Azure DevOps**), tratado como
  **estratégia plugável** (futuro: GitLab, GitHub, outros).
- **R1.5** Configurar **fluxo de branches**: branch base e branches-alvo de PR (e
  regras de fluxo da workspace).
- **R1.6** Configurar **task manager** (no 1.0: **Jira**) como provider plugável.
- **R1.7** Configurar **runtime** (apps, deps, infra, orquestrador) — reusando o
  modelo data-driven atual.
- **R1.8** A aplicação **infere** o que puder (ver [§7](#7-inferência-reduzir-trabalho-e-erro-do-dev))
  para reduzir trabalho e erro do Dev.
- **R1.9** **Testar conexões no momento da criação** (git, task manager, runtime).
- **R1.10** Listagem de workspaces com **edição**: incluir/remover repositórios,
  **re-testar** conexões, **atualizar credenciais** (tokens, chaves SSH).
- **R1.11** **Etapa final = chat com o Claude** para co-construir a workspace:
  estabelecer **regras da workspace** (ex.: "não fazer merge da `desenv` na branch
  de feature"), **regras de fluxo de trabalho**, e **contexto do projeto** (para o
  Claude entender o projeto). O Claude pode **fazer perguntas específicas**.
- **R1.12** Estados de workspace conforme [§5.1](#51-workspace--estados).
- **R1.13** Tudo o que compõe o `config.toml` atual deve ser configurável pela UI
  (o que não for inferido).

### E2 — Desenvolvimento (trabalhar uma demanda)

**User story principal**
> Como Dev, quero um menu para **começar a desenvolver**: seleciono a workspace, vejo
> minhas tasks do task manager com status (no card e no DOP), seleciono um card e caio
> num **chat com o Claude** onde trabalhamos até a demanda ficar `done`; e quero
> **visibilidade rica** do que aconteceu na demanda.

**Requisitos**
- **R2.1** Menu para iniciar desenvolvimento; **seleção de workspace**.
- **R2.2** Após selecionar, **listar as tasks do provider** (Jira) **vinculadas ao
  Dev**, exibindo **status do task manager** e **status do DOP** (`new/doing/done/
  delivered`).
- **R2.3** Selecionar um card → **tela de prompt/chat com o Claude**.
- **R2.4** Dev e Claude interagem até a demanda concluir (→ `done`).
- **R2.5** **Polling periódico** (intervalo configurável) para detectar **PR
  mergeado** e pipeline executada → `done → delivered`. Sem webhook.
- **R2.6** **Painel rico da demanda** (UI/UX avançada) com: repos impactados, branches
  criadas, commits feitos, pipelines executadas, PRs realizados, **quem aprovou o
  PR**, **conflitos ocorridos**.

### E3 — Task Manager Provider (Jira; plugável)

- **R3.1** Interface de **task manager provider** com requisito de plugabilidade
  **igual ao do runtime** (estratégia selecionável por configuração).
- **R3.2** Implementação **Jira** no 1.0: listar tasks do Dev, ler status, vincular ao
  fluxo do DOP. (Futuro: ClickUp e outros — *fora de escopo de implementação no 1.0*.)
- **R3.3** O provider é **inferido pela configuração** da workspace.

### E4 — Git Provider (Azure DevOps; estratégia plugável)

- **R4.1** Provider git como **estratégia** (Azure DevOps no 1.0; pluggable p/ GitLab,
  GitHub, outros). *Base já existe na CLI (`PlatformProvider`).*
- **R4.2** Operações de PR (criar/listar/status/conflito/aprovador) expostas via API.

### E5 — Runtime (data-driven; geração assistida)

- **R5.1** Reusar o runtime data-driven atual (orquestrador plugável; docker_compose).
- **R5.2** **Claude gera** os **Dockerfiles** de dev e o **docker-compose.yaml**
  durante a configuração da workspace.

### E6 — Integração com o Claude (chat/agente)

- **R6.1** Chat com o Claude disponível em dois contextos: **config de workspace**
  (E1.11) e **desenvolvimento por demanda** (E2.3).
- **R6.2** O Claude **opera o DOP** (executa ações via API/CLI) durante a conversa.
- **R6.3** O Claude tem **contexto** da workspace (regras, fluxo, projeto) e da
  demanda corrente.
- **R6.4** Histórico de conversa por demanda/workspace (persistência — ver [§10](#10-decisões-técnicas-em-aberto-a-cargo-do-claude)).
- *Mecanismo técnico de integração (Agent SDK × API × claude CLI): em aberto, §10.*

### E7 — Estrutura de pastas da workspace

- **R7.1** Criar/gerenciar a estrutura de [§5.3](#53-estrutura-de-pastas-da-workspace).
- **R7.2** `docs/{RFC,ADR,prompts}` como repositório de conhecimento da workspace.

### E8 — CLI (cliente fino da API)

- **R8.1** A CLI é a **porta de entrada do Claude** para o DOP: continua oferecendo os
  mesmos comandos `dop ...` de hoje (preserva a forma como o Claude opera).
- **R8.2** A CLI **não acessa a workspace diretamente**; cada comando traduz-se em
  **chamada(s) à API**. A API é quem executa e detém o estado.
- **R8.3** Toda operação do DOP é, portanto, exposta pela **API** e refletida na CLI
  (a CLI nunca tem capacidade que a API não tenha).
- **R8.4** A CLI permanece **ergonômica para uso ocasional do Dev** (responsabilidade
  de produto do **Dev**).

## 7. Inferência (reduzir trabalho e erro do Dev)

A aplicação deve **inferir automaticamente** o máximo possível durante a configuração,
deixando para o Dev apenas o que não dá para deduzir. Candidatos a inferência (a
refinar):
- Provider git e organização/projeto a partir da **URL do remote**.
- Nome/serviço de apps e portas a partir dos repositórios e/ou docker-compose.
- Branch base e alvos de PR a partir do repositório remoto.
- Task manager / chave de projeto a partir de convenções do remote ou do Dev.
- Estrutura de pastas e mapeamento app↔repo a partir do layout em `repos/`.
- Dependências FE→BE e suites e2e a partir da estrutura dos repositórios.

> **Princípio:** *inferir e pedir confirmação* > *perguntar do zero*.

## 8. Requisitos não-funcionais

- **RNF1 — Segurança de segredos:** nunca vazar tokens/credenciais (reusar a
  redação/guard atuais); credenciais armazenadas de forma segura (mecanismo em §10).
- **RNF2 — Idempotência & dry-run:** preservar o comportamento idempotente e o
  `--dry-run` da CLI nas operações expostas.
- **RNF3 — Auditabilidade:** estado e histórico de ações/conversas rastreáveis.
- **RNF4 — Multi-workspace:** isolamento entre workspaces.
- **RNF5 — Plugabilidade:** git, runtime e **task manager** seguem o mesmo padrão de
  provider/estratégia selecionável por configuração.
- **RNF6 — Responsividade do polling:** detecção de merge/pipeline em intervalo
  configurável, sem sobrecarregar APIs externas.
- **RNF7 — Observabilidade:** logs (com redação) por workspace/demanda.
- **RNF8 — Portabilidade:** sem acoplamento a um workspace específico (lição do
  refactor de runtime).

## 9. Mapa de features e divisão de responsabilidades

> Visão preliminar para o fatiamento. Cada feature vira um PRD próprio.
> **Owner** indica quem lidera os **requisitos/decisões** (não exclusividade de execução).

| Feature | Épico(s) | Owner (requisitos) | Notas |
|---|---|---|---|
| F0 — Fundação (3 componentes, integração Claude, persistência, auth) | E6, E8 | **Claude** | Decisões em §10; destrava o resto. |
| F1 — Núcleo + API; CLI vira cliente fino | E8, E4, E5 | **Claude** | Migrar lógica da CLI p/ o núcleo da API; CLI → API. |
| F2 — Task Manager Provider (Jira) | E3 | **Claude** | Espelha providers atuais. |
| F3 — Wizard de Workspace (UI) | E1 | **Dev** (UX) + Claude (infra) | Multi-etapa, save parcial, testes de conexão. |
| F4 — Inferência de configuração | E1, E7 | **Claude** | Reduzir trabalho/erro do Dev. |
| F5 — Chat com o Claude (config + dev) | E6, E1.11, E2.3 | **Dev** (UX) + Claude (motor) | Núcleo da colaboração. |
| F6 — Workflow de Desenvolvimento (lista + painel da demanda) | E2 | **Dev** (UX) + Claude (dados) | UI rica + polling. |
| F7 — Scaffolding de workspace (Dockerfiles/compose) | E5, E7 | **Claude** | Geração assistida. |
| F8 — CLI (ergonomia de uso ocasional) | E8 | **Dev** | Mantida. |

## 10. Decisões técnicas em aberto (a cargo do Claude)

> Estas serão resolvidas por mim (Claude) com liberdade para escolher/testar, e
> documentadas como ADRs quando decididas.

- **D1 — Mecanismo de integração com o Claude:** Claude Agent SDK (Claude Code
  headless) × Anthropic API direta × `claude` CLI. Afeta chat, operação do DOP e
  contexto.
- **D2 — Migração do núcleo e CLI-como-cliente:** como extrair a lógica atual do
  pacote da CLI para um **núcleo consumido pela API**, e como reescrever a CLI como
  **cliente HTTP fino** preservando os comandos `dop ...`. (A direção — CLI → API — é
  requisito fixo; o "como" é decisão técnica.) Inclui como o núcleo reusa
  `git/platform/runtime/core` de hoje.
- **D3 — Stack:** linguagem/framework da API (provável Python p/ reusar a CLI) e do
  Frontend (SPA); empacotamento e execução local.
- **D4 — Persistência:** manter `.state.json` em disco × banco (workspaces, demandas,
  histórico de chat, status DOP). Migração/coexistência com o config atual.
- **D5 — Modelo de uso e auth:** ferramenta **local de um Dev** × **hospedada
  multiusuário** (muda auth, isolamento, segredos).
- **D6 — Armazenamento de segredos:** tokens/chaves SSH (keychain/secret store ×
  arquivo cifrado × variáveis de ambiente, como hoje).
- **D7 — Detecção de merge/pipeline:** Azure REST API × `az` CLI × Claude+Azure MCP.
- **D8 — Tempo real no Frontend:** streaming do chat e atualização de status
  (SSE/WebSocket × polling no front).
- **D9 — Geração de Dockerfiles/compose pelo Claude:** fluxo, validação e versionamento.

## 11. Questões de produto em aberto (a validar com o Dev)

- **P1 — Modelo de uso:** o 1.0 é estritamente **local/single-dev** ou já prevê uso
  por mais de um Dev? (Impacta auth e expectativa de UX.) *Relaciona-se a D5.*
- **P2 — "Done" por quem:** `done` é decisão conjunta Dev+Claude explícita (botão) ou
  inferida (PR criado)? Confirmar gatilho exato.
- **P3 — Escopo de "minhas tasks":** filtro de tasks do Jira (assignee = Dev? sprint
  atual? projeto?).
- **P4 — Edição de workspace ativa:** alterar config de uma workspace `ativa` com
  demandas em andamento — quais campos podem mudar e o que reprocessa?
- **P5 — Onde o chat "mora":** chat por demanda, por workspace, ou ambos? Histórico
  some quando a demanda vira `delivered`?
- **P6 — Multi-repo por demanda na UI:** como a UI apresenta uma demanda que toca N
  repositórios (branches/PRs/pipelines por repo).
- **P7 — Regras da workspace:** formato (texto livre para o Claude × regras
  estruturadas que o DOP também valida).

## 12. Fora de escopo do 1.0

- Webhooks (detecção é por polling).
- Implementações extras de task manager (ClickUp etc.) e git providers (GitLab/GitHub)
  — apenas a **plugabilidade** é requisito.
- Merge/aprovação automática de PR.
- CI/CD remoto gerenciado pelo DOP.
- Multiusuário com RBAC avançado (depende de P1/D5).

## 13. Glossário

- **Workspace:** unidade de configuração de um projeto (repos, providers, runtime,
  regras) onde o trabalho acontece.
- **Demanda / card / task:** item de trabalho originado no task manager (Jira),
  conduzido no DOP.
- **Provider:** implementação plugável de uma integração (git, runtime, task manager).
- **Runtime:** ambiente local de execução (docker-compose hoje) das aplicações.
- **Status DOP:** ciclo `new/doing/done/delivered` (distinto do status do task manager).
- **Inferência:** dedução automática de configuração para reduzir trabalho/erro do Dev.

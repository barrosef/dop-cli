# Prompt para o Replit — Frontend do DOP 1.0 (mock-first)

> **Como usar:** cole o conteúdo da seção "PROMPT" abaixo no Replit Agent. Tudo fora
> dela é nota para o time (Dev/Claude). O frontend é **100% mockado** nesta fase; o
> Claude fará a integração com a API depois. A camada de dados foi desenhada para ser
> trocada por um cliente HTTP real sem reescrever as telas.

---

## PROMPT

Você vai construir o **frontend completo** do **DOP** — uma ferramenta auxiliar ao
desenvolvimento de software *IA-first*, onde um **Dev** e um agente **Claude**
colaboram para conduzir demandas (cards do Jira) do início ao fim. Nesta fase, **use
apenas dados mockados** (sem backend real). O objetivo é uma SPA navegável, polida e
realista.

### 1. Stack (obrigatória)

- **React + Vite + TypeScript**
- **Tailwind CSS** + **shadcn/ui** (Radix) para componentes
- **React Router** para navegação
- **TanStack Query** para data fetching (apontando para a camada de mock)
- **Zustand** para estado de UI leve (ex.: wizard, sessão de chat)
- **lucide-react** para ícones
- Estrutura de pastas:
  ```
  src/
    app/            # rotas/páginas
    components/     # UI reutilizável
    features/       # workspaces, demands, chat, dossier, logs
    lib/
      api/
        types.ts        # TODOS os tipos do domínio (contrato)
        client.ts       # interface DopApi (assinaturas)
        mockClient.ts   # implementação mock (fixtures + latência simulada)
        index.ts        # exporta a instância ativa (mock por enquanto)
      mocks/        # fixtures (workspaces, demands, logs, etc.)
    store/          # zustand
  ```
- **Regra de ouro:** as telas **só conhecem a interface `DopApi`** (`client.ts`),
  nunca os mocks diretamente. Trocar `mockClient` por um `httpClient` no futuro não
  pode exigir mudança nas telas.

### 2. Filosofia de produto (guia de UX)

- O **Dev é gestor do Claude**, não operador. O Claude é autônomo (planeja,
  implementa, testa, gera contexto/memórias, faz análises forenses). O Dev fornece
  contexto/requisitos, decide, aprova e intervém quando chamado.
- **Multi-projeto, monousuário:** o Dev trabalha em várias workspaces/demandas em
  paralelo. O recurso escasso é a **atenção** — a UI deve **direcionar atenção**, não
  exigir varredura.
- **Minimalismo:** mostrar o essencial; **evitar telas e relatórios em excesso**.
  Densidade calma, hierarquia clara, status legíveis (badges, barras de progresso).
- Suporte a **tema claro/escuro**.

### 3. Mapa de telas e rotas

| Rota | Tela |
|---|---|
| `/` | **Home**: lista de workspaces + ação "Nova workspace". Faixa opcional "Onde sou necessário" (demandas que pedem atenção do Dev em qualquer workspace). |
| `/workspaces/new` | **Wizard de criação** de workspace (multi-etapa, salvável por etapa). |
| `/workspaces/:id/edit` | **Edição** da workspace (mesmo wizard, qualquer etapa; re-testar conexões, atualizar credenciais, incluir/remover repos). |
| `/workspaces/:id` | **Visão da workspace**: menu para "Desenvolver" + resumo. |
| `/workspaces/:id/demands` | **Lista de demandas** do Dev (cards do Jira) com duplo status. |
| `/workspaces/:id/demands/:demandId` | **Tela de execução/detalhe da demanda** (chat + wizard de etapas + dossiê + logs). |

### 4. Workspace — wizard

Estados da workspace: `draft` | `active` | `inactive` | `deleted` (badge visível).
O wizard salva **etapa por etapa** e permite editar qualquer etapa (completa ou não).
Cada etapa de conexão tem botão **"Testar conexão"** (mock: retorna sucesso/erro com
latência). Etapas:

1. **Básico** — nome, pasta raiz (root), descrição.
2. **Repositórios git** — lista de repos remotos; por repo: URL e **protocolo
   (http / https / ssh)** com os **campos de credencial conforme o protocolo**
   (login+token para http/https; chave SSH para ssh). **Provider git = Azure DevOps**
   (apresentar como seleção com nota "outros providers em breve"). Botão testar
   conexão por repo.
3. **Fluxo de branches** — por repo: **branch base** e **branches-alvo de PR**; campo
   para **regras de fluxo** (texto).
4. **Task manager** — **Jira** (seleção; nota "outros em breve"): URL, projeto,
   credenciais; testar conexão.
5. **Runtime** — apps (nome, papel frontend/backend, porta), dependências FE→BE, infra
   (ex.: mongodb, mysql). Apresentação simples (lista editável).
6. **Extensões do Claude (secundário)** — adicionar **MCPs** (ex.: postgres, mysql),
   **plugins**, **skills** e **comandos customizados** (nome + descrição); os comandos
   ficam disponíveis por **auto-complete no chat** (ver §6).
7. **Chat de configuração** — uma **tela de chat com o Claude** para definir **regras
   da workspace** (ex.: "não fazer merge da `desenv` na branch de feature"), **regras
   de fluxo de trabalho** e **contexto do projeto**. O Claude (mock) pode fazer
   **perguntas** e ir consolidando as regras num painel lateral.

A Home/listagem permite: editar, **re-testar conexões**, **atualizar credenciais**
(tokens/chaves), incluir/remover repositórios, ativar/inativar/excluir.

### 5. Desenvolvimento — lista de demandas

Ao escolher "Desenvolver" numa workspace, listar **as demandas do Dev** (cards do Jira
mockados). Cada card mostra **dois status**:
- **Status no Jira** (ex.: To Do, In Progress, Code Review, Done…).
- **Status no DOP**: `new` | `doing` | `done` | `delivered` (badge distinto).
  - `new` = não começou no DOP · `doing` = Claude+Dev trabalhando · `done` = PR feito e
    considerado terminado · `delivered` = PR mergeado e pipeline rodou.

Filtros simples (por status DOP, por status Jira, busca por chave). Clicar no card →
tela de execução.

### 6. Tela de execução/detalhe da demanda (núcleo do produto)

Layout em **3 áreas** (responsivo; em telas largas, lado a lado):

**(A) Chat com o Claude** (coluna principal)
- Conversa Dev ↔ Claude (mock). Mensagens com markdown, blocos de código, e
  "ações do Claude" (ex.: "executei `dop demand-init OG-123`", "criei branch X").
- **Auto-complete de comandos customizados**: ao digitar `/`, sugerir os comandos
  configurados na workspace (etapa 6) + comandos padrão.
- Caixa de entrada com envio; indicador de "Claude trabalhando…".

**(B) Wizard de etapas da demanda** (lateral) — **etapas estáticas (MVP)**, mostrando
**etapa atual**, **já executadas** (✓) e **próximas**. As 7 etapas:

1. **Iniciar a demanda** — leitura do card Jira **via MCP**; o humano informa a
   **jira-key** pelo chat.
2. **Contextualização** — Claude e humano interagem; Claude faz **análise forense**,
   busca o que precisa no código-fonte; humano informa dados necessários; Claude
   **monta o contexto**.
3. **Plano** — Claude monta o **plano de desenvolvimento e de testes**.
4. **Execução do plano** — implementação + testes unitários + testes e2e; o Claude
   **aciona o DOP via CLI** que **cria as branches** (como já funciona hoje).
5. **Execução dos testes** —
   - **5.1 Testes unitários:** executa; **ajusta os testes** se falharem por erro de
     teste; **ajusta o código-fonte** se os testes estiverem certos mas a implementação
     falhar.
   - **5.2 Testes e2e:** executa; **ajusta repetidas vezes** (testes e/ou código) até
     **passarem**.
6. **Validação humana** — o humano faz **teste funcional**, interage com o Claude
   (que pode ou não precisar ajustar) e por fim **aprova** a mudança.
7. **Finalização** — o Claude aciona o **DOP** para **commit + push + PRs**, monta uma
   **mensagem .txt simples** com a **lista dos PRs** (sem muitos detalhes, como hoje),
   **finaliza a demanda** e **move o card para a próxima etapa** (as etapas/filtros do
   card são definidos via chat na demanda).

Cada etapa tem estado: `pending` | `running` | `done` | `blocked`. Mostrar progresso e
permitir clicar numa etapa para ver seu resumo. *(Observação: a marcação de conclusão
de etapa pode ser por ação do Claude ou do Dev — trate como dado vindo da API.)*

**(C) Dossiê + Logs** (abas ou painel inferior) — apresentação **enxuta**:
- **Git:** repos impactados, branches criadas, commits.
- **PRs:** enviados e **mergeados**, **quem aprovou**, **conflitos**.
- **Arquivos manipulados:** planos, contextos, **ADRs**, código-fonte criado/alterado.
- **Testes:** unitários e **e2e** criados, com resultado (`success`/`fail`/`skipped`)
  e **barras de progresso** na etapa de testes. **NÃO** implemente a visualização ao
  vivo do Playwright agora (fica para outra fase) — apenas status/contagem/progresso.
- **Tempo:** início, fim, tempo gasto (por demanda; opcional por etapa).
- **Allure:** apenas um **placeholder** "relatório Allure" (sem integração agora).
- **Logs (tempo real, mockado):** três fontes selecionáveis — **(a) aplicações**,
  **(b) testes (unitários/e2e)**, **(c) containers de infra** (mysql/mongo/allure).
  Simular streaming (novas linhas aparecendo); acessível por clique.

### 7. Modelo de dados (TypeScript — em `lib/api/types.ts`)

Defina e use estes tipos (ajuste nomes/campos se melhorar a clareza, mantendo a
intenção). Crie fixtures realistas em `lib/mocks/`.

```ts
type WorkspaceStatus = 'draft' | 'active' | 'inactive' | 'deleted';
type GitProtocol = 'http' | 'https' | 'ssh';
type DopStatus = 'new' | 'doing' | 'done' | 'delivered';
type StageStatus = 'pending' | 'running' | 'done' | 'blocked';
type TestStatus = 'running' | 'success' | 'fail' | 'skipped';

interface RepoConfig {
  id: string; name: string; remoteUrl: string; protocol: GitProtocol;
  credentialRef?: string;                 // referência (nunca o segredo em si)
  baseBranch: string; prTargets: string[]; flowRules?: string;
}
interface TaskManagerConfig { provider: 'jira'; baseUrl: string; project: string; }
interface RuntimeApp { name: string; role: 'frontend' | 'backend'; port: number; dependsOn?: string[]; }
interface ClaudeExtensions {
  mcps: { name: string; kind: string }[];   // ex.: { name:'pg', kind:'postgres' }
  plugins: string[]; skills: string[];
  commands: { name: string; description: string }[];
}
interface Workspace {
  id: string; name: string; root: string; status: WorkspaceStatus;
  gitProvider: 'azure_devops';
  repos: RepoConfig[]; taskManager: TaskManagerConfig;
  runtime: { apps: RuntimeApp[]; infra: string[] };
  claudeExtensions: ClaudeExtensions;
  rules: string[];                          // regras consolidadas (etapa de chat)
  context: string;                          // contexto do projeto
}
interface Stage { key: string; title: string; status: StageStatus; summary?: string; startedAt?: string; finishedAt?: string; }
interface PullRequest { id: string; repo: string; sourceBranch: string; targetBranch: string; url: string; merged: boolean; approver?: string; hasConflict: boolean; }
interface FileTouched { path: string; kind: 'plan' | 'context' | 'adr' | 'source' | 'test'; change: 'created' | 'modified'; }
interface TestResult { name: string; type: 'unit' | 'e2e'; status: TestStatus; }
interface DemandDossier {
  repos: string[]; branches: string[]; commits: number;
  prs: PullRequest[]; files: FileTouched[]; tests: TestResult[];
  startedAt?: string; finishedAt?: string; elapsedSeconds?: number;
}
interface ChatMessage { id: string; author: 'dev' | 'claude'; text: string; at: string; actions?: string[]; }
interface LogLine { source: 'app' | 'test' | 'infra'; service: string; line: string; at: string; }
interface Demand {
  id: string; jiraKey: string; title: string; assignee: string;
  jiraStatus: string; dopStatus: DopStatus;
  stages: Stage[]; dossier: DemandDossier; chat: ChatMessage[];
}
```

### 8. Interface da API (em `lib/api/client.ts`) — mock a implementa

```ts
interface DopApi {
  listWorkspaces(): Promise<Workspace[]>;
  getWorkspace(id: string): Promise<Workspace>;
  saveWorkspace(ws: Partial<Workspace>): Promise<Workspace>;   // upsert por etapa
  testConnection(kind: 'git' | 'jira' | 'runtime', payload: unknown): Promise<{ ok: boolean; message: string }>;
  listDemands(workspaceId: string): Promise<Demand[]>;
  getDemand(workspaceId: string, demandId: string): Promise<Demand>;
  sendChatMessage(demandId: string, text: string): Promise<ChatMessage>;   // mock responde como "claude"
  streamLogs(demandId: string, source: LogLine['source']): AsyncIterable<LogLine>; // ou callback; simule streaming
}
```

O `mockClient` deve simular latência (200–800ms), respostas plausíveis do Claude, e
um "streaming" de logs (novas linhas a cada ~1s). Inclua ~3 workspaces e ~8 demandas
em estados variados (incluindo demandas que pedem atenção do Dev).

### 9. Fora de escopo (NÃO fazer agora)

- Visualização **ao vivo** dos testes e2e (Playwright no browser) — outra fase.
- Backend real, autenticação, multiusuário, deploy.
- Integração real com Jira/Azure/Allure (tudo mock).

### 10. Critérios de aceite

- Navegação completa entre todas as rotas da §3.
- Wizard funcional com salvamento por etapa e edição; botões "testar conexão" (mock).
- Lista de demandas com **duplo status** e filtros.
- Tela de execução com **chat + wizard de 7 etapas + dossiê + logs** (logs simulando
  streaming; 3 fontes).
- UI minimalista, responsiva, tema claro/escuro, sem dados reais.
- Telas dependem **somente** da interface `DopApi` (mock plugável).

---

## Notas para o time (não enviar ao Replit)

- A interface `DopApi` e os `types.ts` são o **contrato preliminar** que o Claude usará
  ao construir a `dop-api`; manter alinhados evita retrabalho na integração.
- Quando a API existir, o Claude troca `mockClient` por um `httpClient` que implementa
  `DopApi` — as telas não mudam.
- A visualização ao vivo dos testes e2e (D11) e o processo de etapas dinâmico são
  evoluções pós-MVP, já registradas na PRD.

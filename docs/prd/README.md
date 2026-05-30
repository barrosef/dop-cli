# Product Requirement Documents (PRD)

Documentos de requisitos de produto do `dop`, reconstruídos por engenharia reversa
das capacidades implementadas. Cada PRD descreve **o problema, os usuários, os
objetivos e os requisitos** de uma capacidade — em complemento aos
[ADRs](../adr/README.md), que descrevem *como* foi resolvido.

> Estes PRDs são **descritivos** (documentam o produto que existe), não
> prescritivos. Itens não implementados aparecem explicitamente em "Fora de escopo"
> ou "Oportunidades futuras".

> 🚧 **Visão futura:** [PRD Base do DOP 1.0 (MVP)](dop-1.0-mvp/README.md) — a evolução
> para produto CLI + API + Frontend (documento vivo, fonte para o fatiamento em features).

| # | Título | Capacidade |
|---|---|---|
| [0001](0001-plataforma-devops-ia-first.md) | Plataforma DevOps IA-First (visão de produto) | Guarda-chuva |
| [0002](0002-ciclo-de-vida-de-demandas.md) | Ciclo de vida de demandas (Jira → branch → PR) | Demanda |
| [0003](0003-integracao-e-resolucao-de-conflitos.md) | Integração diária e resolução de conflitos | Integração |
| [0004](0004-runtime-local.md) | Runtime local de aplicações | Runtime |
| [0005](0005-testes-e2e-e-relatorios.md) | Testes E2E e relatórios | Qualidade |

## Personas

- **Agente de IA (primário):** conduz o fluxo de ponta a ponta via CLI; precisa de
  comandos determinísticos, idempotentes, com saída estruturada e segura.
- **Desenvolvedor/Operador (humano):** supervisiona, resolve conflitos manualmente,
  aprova PRs na plataforma, inspeciona estado e logs.
- **Tech lead / Revisor:** consome PRs, mensagens de Teams e relatórios E2E.

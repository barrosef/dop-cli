# ADR-0012 — E2E com Playwright + Allure 3 e mecanismo de *strikes*

- **Status:** Aceito
- **Componentes:** `runtime/e2e.py`, `runtime/handlers.py` (`handle_e2e`, `handle_codegen`, `handle_report`), `core/state.py` (`record_e2e_run`, *strikes*)
- **Commits:** `17b9e1a`, `6ef9cc4`, `034fb83`, `e04183d`

## Contexto

Validar uma demanda de ponta a ponta exige testes de UI. O time usa Playwright
(via pytest) com relatórios Allure. O agente de IA precisa: rodar testes por
suíte, por chave Jira (descobrindo as suítes automaticamente) ou por arquivo;
gravar resultados isolados por execução; e ter um **freio** contra loops infinitos
de "tentar de novo" quando algo está consistentemente vermelho.

## Decisão

Modelar a execução E2E em `runtime/e2e.py` + handlers:

- **Resolução de alvo** (`resolve_e2e_target`): aceita nome de **suite**, **chave
  Jira** (regex `[A-Z][A-Z0-9]+-\d+`, normalizada p/ filtro `-k`, ex.: `OG-150` →
  `og_150`) ou **caminho de arquivo**.
- **Descoberta por Jira** (`find_suites_for_jira`): varre `e2e/<suite>/tests/`
  procurando arquivos com o fragmento normalizado e roda só as suítes que contêm
  testes daquela chave, na ordem canônica `E2E_SUITE_ORDER`.
- **Execução**: em container `playwright-env` (profile `e2e`), injetando
  `E2E_BASE_URL`/`E2E_API_URL` por suite (mapas de porta FE/BE), com saída Allure em
  `e2e/reports/<jira>/<suite>/run-<N>/results` (`next_run_number` aloca o N).
- **Modo headed** (`--headed`): passa `--headed` ao pytest, exporta `DISPLAY` (X11) e
  seta `E2E_SHARED_CONTEXT=1`.
- **Relatórios Allure 3**: gerados após a execução; `dop report serve|open|clean`
  gerencia o portal e a retenção (mantém últimas N execuções).
- **Codegen**: `dop codegen <suite>` grava interações em
  `tests/recordings/recording_<data>.py`.

**Mecanismo de *strikes*** (`core/state.py`):

- `record_e2e_run(status=...)`: `red` incrementa `strike_count`; `green` zera.
- `--max-strikes N` (1–10, default do config) define o teto; `--reset-strikes`
  zera manualmente. O histórico guarda as últimas 10 execuções.

## Consequências

- ➕ "Rode os E2E da OG-150" funciona sem o usuário saber em quais suítes os testes
  estão.
- ➕ Relatórios isolados por execução (sem sobrescrever histórico).
- ➕ *Strikes* dão ao agente um critério objetivo para **parar de insistir** num teste
  cronicamente vermelho — barreira anti-loop importante em automação por IA.
- ➖ Acoplado ao layout `e2e/<suite>/tests/` e a Allure/Playwright específicos.
- ➖ Headed exige X11 disponível no host.

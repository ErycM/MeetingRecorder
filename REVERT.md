# REVERT — refactor/obsidian-vault-pipeline

Rollback plan para o branch `refactor/obsidian-vault-pipeline` (Onda 1 do plano `C:\Users\erycm\.claude\plans\analise-detalhadamente-todo-o-rippling-pebble.md`).

## Estado de partida

- **Branch base**: `main` @ `7123f84` ("Merge pull request #2 from ErycM/feat/ui-overhaul")
- **Remote**: `origin` = https://github.com/ErycM/MeetingRecorder.git
- **Test baseline**: 323 passed, 2 failed, 4 skipped (25.39s) — as 2 falhas são `test_live_mutex_first_acquire` e `test_live_lockfile_written_and_removed` em `tests/test_single_instance.py::TestWindowsLive`. Esse baseline deve ser preservado.
- **User config backup**: `C:\Users\erycm\.claude\plans\config-backup-2026-04-19.toml` (o `config.toml` produção do usuário antes do refactor).

## Como desfazer tudo (rollback completo)

```bash
# 1. Voltar para o main sem perder nada
cd "C:/Users/erycm/SaveLiveCaptions"
git checkout main

# 2. (opcional) apagar o branch do refactor
git branch -D refactor/obsidian-vault-pipeline

# 3. (se o config.toml do usuário foi modificado) restaurar do backup
cp "C:/Users/erycm/.claude/plans/config-backup-2026-04-19.toml" "$APPDATA/MeetingRecorder/config.toml"

# 4. Validar que pytest volta ao baseline
pytest --tb=no -q
```

## Como desfazer só o último commit

```bash
git reset --hard HEAD^
```

## Regras operacionais (não violar)

1. Nunca comitar direto no `main` — só no `refactor/obsidian-vault-pipeline`.
2. 1 commit por sub-etapa (1.1, 1.2, 1.3, 1.4).
3. Após cada commit: rodar `pytest --tb=no -q`. Se regredir abaixo de `323 passed, 2 failed`, **reverter** (`git reset --hard HEAD^`) e investigar.
4. Não reconstruir `dist/*.exe` até todas as etapas passarem.
5. Qualquer sinal de regressão visível (app não abre, HistoryTab quebra, tray desaparece): **reverter** antes de debugar.

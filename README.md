# Codex-
ix_logs_sqlite 是一个 Windows 命令行工具，用于解决 Codex desktop 的 TRACE 日志高频写盘问题。它自动备份 ~/.codex/logs_2.sqlite，通过 SQLite trigger 静默拦截所有 level=TRACE 的 INSERT，然后执行 WAL checkpoint 并截断，最后采样验证不再增长。整个过程对 Codex 应用本身零影响，无报错。

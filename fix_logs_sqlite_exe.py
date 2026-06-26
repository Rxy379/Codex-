# Codex-
ix_logs_sqlite 是一个 Windows 命令行工具，用于解决 Codex desktop 的 TRACE 日志高频写盘问题。它自动备份 ~/.codex/logs_2.sqlite，通过 SQLite trigger 静默拦截所有 level=TRACE 的 INSERT，然后执行 WAL checkpoint 并截断，最后采样验证不再增长。整个过程对 Codex 应用本身零影响，无报错。

# Codex Desktop TRACE 日志高频写盘问题的排查与解决

## 问题现象

使用 Codex desktop 一段时间后，发现 `~/.codex/logs_2.sqlite` 这个 SQLite 数据库持续膨胀，体积达到几十 MB，同时伴随 `.sqlite-wal`（WAL 文件）也不断增长。磁盘 I/O 明显升高，尤其是在长时间对话后。

## 排查过程

### 1. 检查数据库表结构和日志分布

通过 SQLite 查看日志表的级别分布：

```sql
SELECT level, COUNT(*) FROM logs GROUP BY level ORDER BY COUNT(*) DESC;
```

结果触目惊心：

| 级别 | 数量 | 占比 |
|------|------|------|
| TRACE | 5,976 | 38.1% |
| INFO | ~8,000 | ~52% |
| DEBUG | ~1,300 | ~9% |
| WARN/ERROR | ~200 | <2% |

TRACE 占到了接近 **四成**。

### 2. 查看近期的写入趋势

进一步采样最近写入的 200 条日志：

```
最近 200 条日志中，TRACE 占比 98%
写入速率：约 1.2 条/秒，高峰期达 21.6 条/秒
```

而且数据库 ID 已经跑到 50 万+，虽然有后台清理机制将总行数维持在 15,000 行左右，但**每一秒都在 INSERT + DELETE 循环刷盘**，WAL 膨胀到 6 MB。

### 3. 根因确认

Codex desktop 在运行过程中会持续输出 TRACE 级别日志到 SQLite，这部分日志对普通用户没有调试价值，但占用了大量磁盘写入。确认问题后，决定通过 SQLite trigger 在数据库层面静默拦截 TRACE 级别的 INSERT。

## 解决方案

### 原理

在 `logs` 表上创建一个 `BEFORE INSERT` 触发器：

```sql
CREATE TRIGGER IF NOT EXISTS block_trace_inserts
BEFORE INSERT ON logs
WHEN NEW.level = '"'"'TRACE'"'"'
BEGIN
    SELECT RAISE(IGNORE);
END
```

当有新的 TRACE 日志要插入时，`RAISE(IGNORE)` 会静默跳过该行，INSERT 语句对应用层返回成功，但实际数据不写入。**Codex desktop 不会感知到任何异常**。

### 工具使用

写了一个自包含的 Windows exe，下载解压后双击即可运行：

1. **自动备份** — 在 `~/.codex/backup/` 下保存带时间戳的完整数据库备份
2. **注入 trigger** — 创建 `block_trace_inserts` 触发器
3. **WAL checkpoint** — 执行 checkpoint 并截断 WAL 文件
4. **验证** — 5 秒采样，确认 MAX(id) 和 WAL 不再因 TRACE 增长

### 注意事项

- 运行前请**退出 Codex desktop**，否则数据库被锁定无法写入
- 如需撤销，用 DB Browser for SQLite 或 sqlite3 执行：`DROP TRIGGER IF EXISTS block_trace_inserts;`
- 工具只会拦截后续写入，已存在的 TRACE 日志不会被删除

## 后记

这个工具用 PyInstaller 编译为单文件 exe，无需 Python 环境即可运行。如果你也遇到 Codex 日志膨胀的问题，欢迎使用。

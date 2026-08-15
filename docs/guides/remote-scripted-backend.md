# 远程脚本后端 —— 扮演模型

`RemoteScriptedProvider` 允许外部调用方通过 HTTP 扮演 Pivot 的 LLM。用途：

- 逐步手动驱动 agent，调试系统提示词、工具接线或框架集成。
- 让第二个程序以确定性方式冒充模型，而无需预先编写脚本规则。
- 无需付费 token 即可对嵌入方案（GameAgents、自定义编排器）进行冒烟测试。

该后端位于 `pivotcode/providers/remote_scripted_provider.py`。通过以下方式选择：

```bash
pivotcode --backend scripted --model remote
```

或者从 Python：

```python
agent = PivotCodeAgent(backend="scripted", model="remote", ...)
```

agent 启动时，你会看到 stdout 上出现两行：

```
[remote-scripted] LLM endpoint: http://127.0.0.1:8430
[remote-scripted] bound to session <sid8> (cwd=...)
```

端口 `8430` 是默认值；如果被占用，供应商会向上扫描（最高 `8450`）。

## 端点

所有端点都位于 `http://127.0.0.1:<port>` 之下。没有认证——服务器只绑定到 `127.0.0.1`。

| 方法 | 路径 | 用途 |
|--------|------------------|---------|
| GET    | `/api/health`    | 服务器就绪后返回 `{"ok": true}`。 |
| GET    | `/api/session`   | 会话元数据：`session_id`、`cwd`、`model`、`port`、`calls_served`。 |
| GET    | `/api/pending`   | 当前正在等待响应的 LLM 调用。空闲时返回 `204 No Content`。 |
| POST   | `/api/respond`   | 提交助手的响应。解除 `stream()` 阻塞并返回 `{"accepted": true}`。 |

### 待处理载荷（GET `/api/pending`）

```json
{
  "request_id": "remote-req-3-a1b2c3d4",
  "turn": 3,
  "model": "remote",
  "max_tokens": 16000,
  "thinking": {"type": "disabled", "budget_tokens": null},
  "stop_sequences": null,
  "system": ["You are a coding agent..."],
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": [...]},
    {"role": "tool", "tool_call_id": "...", "content": "..."}
  ],
  "tools": [
    {"name": "Bash", "description": "...", "input_schema": {...}},
    ...
  ],
  "session_id": "ce907458de4a4307...",
  "cwd": "/path/to/work_dir"
}
```

调用进入待处理状态的同一时刻，该载荷会镜像到 `<cwd>/.pivot/sessions/<session_id>/remote_inbox.json`。读取任一路径均可——两者相同。运行结束后该文件仍然保留，保存着最后一份待处理快照。

### 响应载荷（POST `/api/respond`）

```json
{
  "text": "I'll list the directory.",
  "tool_calls": [
    {"name": "Bash", "input": {"command": "ls -la"}}
  ],
  "thinking": null,
  "stop_reason": "tool_use",
  "usage": {"input_tokens": 100, "output_tokens": 50}
}
```

所有字段都是可选的：

- `text` —— 助手文本（普通字符串）。
- `tool_calls` —— `{"name": "ToolName", "input": {...}}` 列表。每一项都会获得自动生成的 `tool_use` id；传入 `"id": "toolu_..."` 可覆盖。
- `thinking` —— 可选的思考文本（作为 `StreamThinkingDelta` 发出）。
- `stop_reason` —— `"end_turn"`、`"tool_use"`、`"max_tokens"` 等。省略时根据请求体自动推断（`tool_calls` 非空则为 `tool_use`，否则为 `end_turn`）。
- `usage` —— token 计数。可选；省略时默认为零。

错误：

```json
{"error": "rate limit hit", "error_type": "overloaded", "status_code": 529}
```

会为 agent 产生一个 `StreamError` 事件，与真实供应商失败完全一致。

## Shell 宏

`scripts/pivot-remote-macros.sh` 附带一组包装这些端点的 shell 函数。每个终端 source 一次：

```bash
source ~/projects/Pivot-Code-agent/scripts/pivot-remote-macros.sh
pivot-help    # list available commands
```

然后驱动一次实验：

```bash
pivot-pending-last       # latest message Pivot got
pivot-bash 'ls -la'      # call the Bash tool
pivot-wait               # block until next pending call
pivot-text "I'm done."   # text-only turn
pivot-exit               # ExitTask, ends the experiment
```

默认端口是 `8430`；source 之前可用 `ALAN_PORT=8431` 覆盖。

## 典型交互循环

```bash
PORT=8430

# 1. See what the model is being asked.
curl -s http://127.0.0.1:$PORT/api/pending | jq

# 2. Send a text-only response (ends the turn).
curl -s -X POST http://127.0.0.1:$PORT/api/respond \
  -H 'Content-Type: application/json' \
  -d '{"text": "acknowledged"}'

# 3. Or call a tool.
curl -s -X POST http://127.0.0.1:$PORT/api/respond \
  -H 'Content-Type: application/json' \
  -d '{"text": "running ls", "tool_calls": [{"name": "Bash", "input": {"command": "ls"}}]}'

# 4. Poll until the next call is pending.
while [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/api/pending)" != "200" ]; do
  sleep 0.3
done
```

当上一个响应仍在处理中（工具执行、框架簿记）时，`GET /api/pending` 返回 `204`。当下一个 LLM 调用进入待处理状态时，它切换为 `200`。最简单的模式就是轮询直到 `200`。

## 会话关闭

供应商的 HTTP 服务器运行在一个守护线程上。它在以下情况关闭：

- agent 调用 `agent.close()`（正常生命周期）。
- 进程退出。

关闭后端口释放，`/api/health` 变得不可达。这就是会话结束的信号。

## 并发会话

如果两个 agent 同时请求同一个端口，第二个会选择下一个空闲端口（8431、8432，……）。每个 agent 的服务器相互独立。会话 id 和 cwd 通过 `/api/session` 暴露，因此你可以区分正在与哪个 agent 对话。

## 在 Python 内部

该供应商也可以不经 HTTP 直接检查和驱动：

```python
agent = PivotCodeAgent(backend="scripted", model="remote", ...)
provider = agent._provider  # RemoteScriptedProvider instance
print(provider._port)
```

……但 HTTP API 才是受支持的接口，也是工具应该面向的目标。

## 相关

- [reference/python-api.md](../reference/python-api.md) —— `PivotCodeAgent` 构造函数。
- [CHANGELOG.md](../../CHANGELOG.md) —— 2026-05-11 的条目。
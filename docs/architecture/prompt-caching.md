# 提示词缓存

提示词缓存让供应商可以复用先前计算过的前缀，缓存 token 的输入成本最高可降低 90%。对于多轮代理会话，这是最大的单一成本杠杆。

## 它的工作原理

缓存是供应商特定的。Pivot Code 在可能的地方应用缓存标记，其余交给供应商处理：

- **Anthropic**（直连）：在内容块上打 `cache_control: {"type": "ephemeral"}` 标记。标记之前的前缀会被缓存。每个请求最多 4 个断点。缓存命中成本为常规输入的 10%；写入成本为 1.25 倍。
- **OpenAI**：自动的基于前缀的缓存。无需标记。
- **OpenRouter → Anthropic**：将 `cache_control` 透传给 Anthropic 的 API。机制相同。
- **本地模型**：无缓存。

## Pivot 的缓存策略

### Anthropic 供应商（`anthropic_provider.py`）

每个请求放置最多 4 个 `cache_control` 断点：

1. **最后一个工具定义** — 缓存所有工具 schema（约 5–10K token）
2. **最后一个静态系统提示词区块** — 缓存工具 + 稳定的提示词区块（引言、规则、指南）
3. **最后一个系统提示词区块** — 缓存工具 + 包含动态区块的完整系统提示词
4. **最后一条 assistant 消息** — 缓存整个对话前缀

系统提示词被拆分为静态（区块 0–6，各次调用字节完全相同）和动态（区块 7+，会话内稳定，但会因记忆/技能/PIVOT.md 更新而改变）两部分。这种拆分通过 `get_system_prompt()` 返回的 `system_static_boundary` 传达。

### LiteLLM 供应商（`litellm_provider.py`）

使用相同的 `cache_control` 标记，注入到系统消息内容块、工具定义和 assistant 消息中。LiteLLM 将其透传给支持它们的供应商，对不支持的供应商则忽略。

## 缓存失效

会使部分缓存失效的变更：

| 变更 | 失效的断点 | 仍然缓存的 |
|---|---|---|
| 保存记忆（`/save`、密集模式） | BP3（动态系统提示词） | BP1（工具）、BP2（静态系统提示词） |
| 创建/移除技能 | BP3 | BP1、BP2 |
| 编辑 PIVOT.md | BP3 | BP1、BP2 |
| 新的用户消息（正常轮） | BP4（对话） | BP1、BP2、BP3 |
| 切换模型（`/model`） | 全部（不同的缓存空间） | 无 |

## 相关

- [reference/cost.md](../reference/cost.md) — 状态行数字的含义。
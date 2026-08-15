# 记忆

记忆是 Pivot Code 用于**跨会话持久化信息**的机制。与对话历史（按会话存在，且会被压缩）不同，记忆以 markdown 文件形式存于磁盘，并在启用记忆的每个会话开始时加载。

## 「记忆」是什么（以及不是什么）

记忆**不是**：
- 阅读代码的替代品——关于代码库的事实应该通过实际查看文件来获得。
- 发生事件的日志——`git log` 和会话记录已经承担了这个职责。
- 存放临时会话状态的地方。

记忆**是**：
- 用户偏好（「我喜欢简洁的回复，不要在每条消息末尾做总结」）。
- 不明显的项目背景（「认证中间件的重写是出于法律原因，而非技术债」）。
- 代码里没有的工作流程（「要运行测试，先用 `docker-compose up -d db` 启动数据库」）。
- 指向外部系统的线索（「流水线 bug 在 Linear 项目的 INGEST 中跟踪」）。

这种区分很重要，因为记忆是稀缺资源（每个会话都要消耗 token），而且过时的记忆危害很大——智能体会基于可能已不再成立的事实做决策。

## 记忆模式

通过 `--memory <mode>` 或 `/memory <mode>` 设置：

| 模式 | 启动时读取 | 写入 |
|---|---|---|
| **`off`** *(默认)* | 否 | 否 |
| **`on`** | 是 | 用户请求或 `/save` 时 |
| **`intensive`** | 是 | 重要轮次后主动写入 + `/save` 时 |

### `off`（默认）

记忆完全禁用。智能体被告知：*「记忆在当前会话中已禁用。不要尝试读取或写入记忆文件。如果用户要求保存某些内容，告诉他们可以通过 `/memory on` 或 `/memory intensive` 启用记忆。」*

这是当前的默认设置，因为**记忆是一项有成本的特性**：每次加载到系统提示中的记忆都会在每一轮消耗 token。维护不善的记忆系统可能是净负值。

### `on`

记忆在会话开始时加载，并且只在你明确要求时（通过自然语言——「记住我偏好 X」）或运行 `/save` 时写入。对于大多数希望持久化又不想要意外写入的用户来说，这是正确的模式。

### `intensive`

记忆仍然在启动时加载，但智能体还会在重要轮次后主动保存。系统提示指示它留意：
- 对你方法的纠正（「停止做 X」「不要做 Y」）。
- 关于项目方向、架构或工作流程的决策。
- 关于用户角色、偏好或专长的信息。
- 外部系统引用。
- 它学到的构建/测试/部署流程。

每 10 次迭代（`memory_reminder_threshold = 10`），Pivot 会注入一条提醒：*「距上次记忆更新已过去好几轮。请考虑是否有最近的纠正、决策或偏好值得保存。」*

该模式最适合希望 Pivot 随时间积累其工作方式的长期协作者。

## 记忆存放位置

两个作用域：

- **项目记忆**位于 `<cwd>/.pivot/memory/`——仅针对当前项目。
- **全局记忆**位于 `~/.pivot/memory/`——在此机器上的所有项目间共享。

两个目录结构相同：

```
memory/
├── MEMORY.md              # Index — loaded at session start
├── user/                  # Who the user is (global scope mostly)
├── feedback/              # Corrections and validated approaches (global scope mostly)
├── project/               # This project's decisions and ongoing work (project scope)
├── reference/             # External system pointers (project scope)
└── workflow/              # Build/test/deploy procedures (project scope)
```

`MEMORY.md` 是一个索引（每条记忆一行，带简短说明）：

```markdown
- [User prefers concise](user/user-prefers-concise.md) — Terse replies, no trailing summaries
- [Migration uses temp table](project/migration-temp-table.md) — Why the 0042 migration uses a staging table
```

单个记忆文件以 YAML frontmatter 开头：

```markdown
---
name: User prefers concise responses
description: Terse replies, no trailing "I did X" summaries
type: feedback
---

Keep responses short and direct. Don't add "Let me know if you want me to explain further" or "Hope this helps!" at the end.

**Why:** User said they can read the diff and find followups boring.
**How to apply:** Every response. Applies to code and prose alike.
```

## 五种记忆类型

| 类型 | 捕获内容 | 作用域倾向 |
|---|---|---|
| **`user`** | 用户的角色、目标、专长、偏好。 | 全局 |
| **`feedback`** | 用户给出的规则——包括纠正和确认。 | 全局 |
| **`project`** | 进行中的工作、决策、事件、动机。 | 项目 |
| **`reference`** | 外部系统线索（Linear 项目、Slack 频道、仪表盘）。 | 项目 |
| **`workflow`** | 构建/测试/部署/开发流程。 | 项目 |

这些类型是软分类——记忆的类型影响提示指导（如何组织正文、何时保存），但不影响技术行为。所有类型都会被统一加载。

## 活文档，而非追加式日志

智能体被指示**就地更新记忆，而不是追加**。当事实发生变化时：
- 已有记忆 → 使用 `Edit` 工具更新相关行。
- 完全被取代 → 使用 `Bash rm` 删除，并 `Edit MEMORY.md` 删除对应行。
- 真正的新主题 → 用 `Write` 写新文件，并 `Edit MEMORY.md` 添加索引条目。

这就是 Pivot 默认 `memory=off` 的原因：维护不善、只会单调增长的记忆比没有记忆更糟。提示明确告诉智能体优先更新而非新建文件。

## `/save`

```
> /save
```

触发一条调用智能体的提示：*「用户请求了记忆更新。回顾最近的对话，找出值得保存或更新的信息。优先使用 Edit（全新文件用 Write）就地修改现有条目，而不是追加会重复或取代它们的新条目。删除或重写过时的条目，而不是让它们与更新的事实并存。」*

带参数时：`/save the deploy process changed`。文本会被追加以提供焦点。

## 访问记忆

Pivot 的系统提示（在 `on` / `intensive` 模式下）包含：

1. 描述保存什么、何时保存以及如何保存的记忆指令。
2. 两个 `MEMORY.md` 文件的内容（先全局后项目），这样智能体无需列出目录就能知道有什么可用。

单个记忆文件**不会**被加载——当 `MEMORY.md` 的描述提示相关内容时，智能体会按需使用 `Read` 工具读取。

回忆时，Pivot 被指示**先验证再行动**：一条说「`x` 函数在 `foo.py` 中」的记忆可能已过时；智能体应先 grep 确认，再基于它提出任何建议。

## 全局 vs 项目

- `~/.pivot/memory/` 适用于此机器上的**每一个** Pivot 会话。用于用户偏好和通用反馈。
- `<project>/.pivot/memory/` 仅适用于此项目。用于项目特定的决策、外部引用和工作流程。

启用记忆时两者会同时加载。主题重叠时项目记忆优先——由智能体根据描述判断哪个适用。

## 调优

| 设置 | 默认值 | 作用 |
|---|---|---|
| `memory` | `off` | 模式：`off`、`on`、`intensive`。 |
| `memory_reminder_threshold` | 10 | 记忆保存提醒之间的迭代数（仅 intensive 模式）。 |

## 相关

- [reference/slash-commands.md](../reference/slash-commands.md) —— `/memory`、`/save`。
- [concepts/project-context.md](project-context.md) —— `PIVOT.md`（静态的按项目指令，而非记忆）。
- [architecture/query-loop.md](../architecture/query-loop.md) 中的 `/save` 流程。
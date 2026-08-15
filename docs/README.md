# Pivot Code — 文档

欢迎。Pivot Code 是一个 Python 编码代理，你可以把它当作**命令行**、**浏览器图形界面**或**Python 库**来使用。本文档按照 [Diátaxis 框架](https://diataxis.fr/) 分为四个轨道：

| 轨道 | 是什么 | 何时阅读… |
|---|---|---|
| **入门** | 单一线性演练 | 你刚安装完 Pivot，想端到端看它工作。 |
| **概念** | 事物*本来*如何 | 你想理解——心智模型、术语、每个子系统做什么。 |
| **指南** | 如何*完成*特定任务 | 你有具体目标（配置本地模型、构建自定义代理、配置钩子）。 |
| **参考** | 详尽查阅 | 你知道自己需要什么，想要确切的名称/默认值/模式。 |
| **架构** | 面向贡献者 | 你在阅读代码或在其上构建。 |
| **贡献** | 流程 | 你想提交 PR 或发布版本。 |

## 入口

- **Pivot 新手？** → [入门](getting-started.md)
- **查阅斜杠命令？** → [reference/slash-commands.md](reference/slash-commands.md)
- **查阅 CLI 标志？** → [reference/cli.md](reference/cli.md)
- **接入本地模型？** → [reference/local-models.md](reference/local-models.md)
- **用 Python 构建代理？** → [guides/building-agents.md](guides/building-agents.md) 和 [reference/python-api.md](reference/python-api.md)

## 概念

- [代理循环](concepts/agent-loop.md) — 轮次、迭代、会话，以及它们之间的关系
- [工具与权限](concepts/tools-and-permissions.md) — 工具如何运行，以及何时询问用户
- [上下文与压缩](concepts/context-and-compaction.md) — 3 个压缩层以及如何调优
- [记忆](concepts/memory.md) — `off` / `on` / `intensive` 模式及其存储内容
- [技能](concepts/skills.md) — 代理可调用的用户自定义提示词模板
- [项目上下文（PIVOT.md）](concepts/project-context.md) — 自动加载到系统提示词中的每个项目的说明
- [Git 树（AGT）](concepts/git-tree.md) — `/move`、`/revert`、`/convrevert`、`/allrevert`

## 指南

- [使用图形界面](guides/using-the-gui.md)
- [配置](guides/configuration.md) — settings.json 优先级链
- [钩子](guides/hooks.md) — 工具使用前后的回调
- [构建代理](guides/building-agents.md) — 把 `PivotCodeAgent` 当作库使用

## 参考

- [CLI 标志](reference/cli.md)
- [斜杠命令](reference/slash-commands.md)
- [工具](reference/tools.md) — 每个内置工具，带模式与示例
- [设置](reference/settings.md) — 每个 `.pivot/settings.json` 键
- [后端与供应商](reference/providers.md) — `auto`（LiteLLM，其余）、`anthropic-native`（Claude 直连 SDK）、`scripted`
- [本地模型](reference/local-models.md) — vLLM / SGLang / Ollama / llama.cpp
- [成本追踪](reference/cost.md) — "Session" 一行的含义
- [Python API](reference/python-api.md) — `PivotCodeAgent` 及相关内容

## 架构（面向贡献者）

- [概述](architecture/overview.md)
- [查询循环](architecture/query-loop.md)
- [系统提示词组装](architecture/system-prompt.md)
- [消息与 API 载荷](architecture/messages-and-api.md)
- [提示词缓存](architecture/prompt-caching.md)

## 贡献

- [开发环境搭建](contributing/development.md)
- [测试](contributing/testing.md)
- [发布流程](contributing/release.md)
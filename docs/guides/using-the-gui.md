# 使用图形界面

`pivotcode --gui` 会在 `http://localhost:8420/` 启动一个本地浏览器界面。它与 CLI 共享同一个 agent 核心——终端里能做的浏览器里都能做——但额外增加了三个面板，提供更丰富的视图。

## 启动

```bash
pivotcode --gui
```

你会看到：

```
  GUI: http://localhost:8420/<project-slug>/

  Open the URL in your browser. All interaction happens there.
```

打开这个 URL。WebSocket 握手完成期间，标签页可能会短暂显示「Connecting…」——这包括首次导入 LiteLLM 的时间（冷启动 Python 约 1.5 秒）。

## 三个面板

### Chat

主要交互面板。流程与 CLI 相同：

- 在底部输入，按 Enter。
- 助手响应逐 token 流式输出。
- 工具调用渲染为带标题的方框。
- 工具结果内联渲染——Edit/Write 显示带行号的绿色/红色统一差异。
- 每轮之后显示成本汇总。

快捷键：**Shift+Enter** 插入换行，**Enter** 提交。

### LLM Perspective

显示 Pivot 每轮发送给模型的**精确载荷**——系统提示词加上完整的 `messages=[...]` 列表。当 agent 的响应出乎你的意料时，这是权威的调试视图：

- 「它为什么调用那个工具？」→ 在系统提示词部分查看工具。
- 「它为什么忘了我们说过的话？」→ 检查是否发生了压缩（寻找 `COMPACT_BOUNDARY` 系统消息）。
- 「模型实际看到了什么上下文？」→ 阅读渲染出的消息。

在调优技能、诊断幻觉或逆向工程奇怪的模型行为时非常有用。

### Git Tree

可视化提交图以及 agent 在其中的轨迹。点击任意节点即可选中；四个操作按钮会亮起：

- **Move to commit** → `/move <sha>`
- **Revert repo to** → `/revert-to <sha>`（破坏性）
- **Revert conv. to** → `/convrevert`（仅对话）
- **Revert all to** → `/allrevert`（两者都）

颜色图例和语义参见 [concepts/git-tree.md](../concepts/git-tree.md)。

右上角有一个用于分支跳跃箭头的**曲率滑块**，以及一个显示每种颜色含义的图例。

## 显示和隐藏面板

顶栏的切换按钮可以隐藏任意面板——当你想要一个不被 Git Tree 占用空间的宽幅 Chat 视图时很有用。

## 权限提示

当工具需要批准时，会出现一个模态框，包含：
- 工具名称和输入字典。
- **Allow / Deny** 按钮。
- 对于 Bash：还有一个 **Allow always "<prefix> *" commands** 第三选项，将该模式记录到 `.pivot/allow_rules.json`。
- 一个自由文本字段：输入你自己的答案，作为「工具结果」发送给模型。

在运行 `pivotcode` 的终端中按 Ctrl+C，或关闭标签页，都会干净地中止当前轮。

## 重新连接

如果你关闭标签页再重新打开，浏览器会通过 WebSocket 重新连接，服务器会重放当前会话的事件历史——chat、LLM perspective 和 git tree 都会自动重新填充。

如果你在刷新标签页之前重启 `pivotcode`，新服务器的历史会替换旧的历史（前端会先收到一个 `reset` 事件）。当 `app.js` / `style.css` 在两次启动之间发生变化时，需要硬刷新（**Ctrl+Shift+R**）——静态资源被激进地缓存。

## 已知限制

- **浏览器会限制后台标签页的定时器。** 如果你让图形界面标签页留在后台并重启 `pivotcode`，「Disconnected — reconnecting…」状态可能需要 10–60 秒才会重试。点击回到标签页可强制立即重连。
- **目前没有 CORS 检查。** 服务器只绑定到 `127.0.0.1`，但如果你通过 SSH 转发端口，SSH 客户端主机上的任何人都可以连接。不要暴露给不受信任的网络。
- **没有认证。** 任何能访问你机器上 `localhost:8420` 的人都可以驱动你的 agent。
- **Git Tree 仍在开发中** —— 合并和分离 HEAD 周围的一些边界情况尚未完全处理。对线性历史和简单分支效果良好。

## 关闭

- 在 Chat 面板中输入 `/exit` —— 干净关闭。图形界面关闭，`pivotcode` 进程退出。
- 关闭标签页 —— 服务器继续运行；重新打开 URL 即可重连。
- 在终端中按 Ctrl+C —— 强制退出。可能会打印回溯信息（参见 [reference/cli.md](../reference/cli.md) 中的已知问题说明）。

## 相关

- [concepts/git-tree.md](../concepts/git-tree.md) —— AGT 语义。
- [reference/slash-commands.md](../reference/slash-commands.md) —— 所有斜杠命令。
- [reference/cli.md](../reference/cli.md) —— `--gui`、`--resume` 及其他标志。
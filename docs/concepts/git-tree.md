# Git 树（AGT）

**Agentic Git Tree（AGT，智能体 Git 树）**是 Pivot 将 git 作为一等状态的处理方式。与将 git 视为旁路的传统开发工具不同，AGT 跟踪智能体在仓库中的位置、它已做的提交，并允许你将**文件与对话**一起移动或回退。

AGT 正在开发中。它可用，但用户体验仍在打磨，尤其是 GUI 的树面板。

## 术语

| 术语 | 定义 |
|---|---|
| **智能体位置** | Pivot 视为「当前」的 SHA。通常等于 `HEAD`，但在外部提交或回退后可能偏离。 |
| **会话根** | 会话开始时 `HEAD` 所在的 SHA。锚定树视图。 |
| **Pivot 提交** | 本会话中智能体通过 `GitCommit` 工具进行的提交。单独跟踪，以便 GUI 将其标为蓝色。 |
| **对话路径** | 智能体「到过」的 SHA 有序列表——在提交图中的轨迹。用于计算 `/convrevert` 的「回退步数」。 |

以上所有内容都持久化在 `.pivot/sessions/<id>/state.json` 中。

## 四个移动命令

### `/move <ref>`

将智能体的位置移动到另一个提交或分支——本质上是 `git checkout`，但 Pivot 还会更新其位置跟踪并注入一条提醒，让智能体知道工作树已更改：

```
<system-reminder>User ran /move, checking out commit abc1234def (ref 'main'). The working tree now reflects that commit — files on disk may have changed compared to what you saw earlier. … Re-read files before making assumptions about their current state.</system-reminder>
```

安全：`/move` 保留提交。它只是一次 checkout。

### `/revert [N]`

通过 `git reset --hard HEAD~N` 在当前分支上破坏性地回退 N 个提交。这些提交会从分支中移除（仍可在约 30 天内通过 `git reflog` 恢复）。

特殊情况：如果工作树不干净且 `N=1`，则只丢弃未提交的更改，不触碰提交。

对话**不受**影响——智能体仍然记得发生了什么。只有仓库会移动。

### `/convrevert [N]`

**对话**回退：从对话中丢弃最近 N 次用户↔智能体交流，但保持工作树不变。适用于「我们走错了路，让我换个角度重新开始这一轮」而不丢失我们接触过的文件。

### `/allrevert [N]`

两者同时进行：回退工作树，并按相应步数截断对话。当你想要彻底重来时使用——智能体对岔路的记忆以及它产生的文件都会消失。

## 为什么是不同工具而非标志位

| 命令 | 仓库状态 | 对话 |
|---|---|---|
| `/move <ref>` | → 新提交 | 不变 |
| `/revert N` | → 回退 N 个提交 | 不变 |
| `/convrevert N` | 不变 | → 回退 N 步 |
| `/allrevert N` | → 回退 N 个提交 | → 回退 N 步 |

一个 2×2 的四个角落。每对轴都是合法的用例：有时你想探索不同分支而不丢失上下文，有时你想忘掉最近的交流但保留文件，有时两者都要。

## 安全：绝不触碰 `.pivot/`

所有 AGT 操作都会在工作树清理中过滤掉 `.pivot/`（`git clean -fd -e .pivot`）。因此你的会话状态、记忆、技能和允许规则都能在回退后存活。没有这个防护，`/revert` 会摧毁会话本身。

Pivot 还会在会话开始时确保 `.pivot/` 在你的 `.gitignore` 中。如果没有，你会看到 `[WARNING]`，并且会话会拒绝某些破坏性操作，直到它被加入。

## 记忆快照

在任何破坏性操作之前，Pivot 会拍摄记忆快照——一份标记了你即将离开的 SHA 的 `.pivot/memory/` 副本。操作之后，它会恢复与目标 SHA 对应的快照（如果存在的话）。

这意味着：如果你编辑记忆、提交，然后 `/revert`，你的记忆会恢复到较旧提交时的状态。这正是「Pivot 提交」能良好工作的原因——智能体的心智状态随仓库一起移动。

存储在 `.pivot/sessions/<id>/memory_snapshots/<sha>/`。会话结束时清理。

## GUI 的 Git 树面板

当你用 `--gui` 启动时，第三个面板显示提交图，包含：

- **蓝色节点**：Pivot 提交（本会话中由 `GitCommit` 创建）。
- **灰色节点**：外部提交（你在会话之外创建的）。
- **虚线节点**：未提交的更改。
- **蓝色线条**：对话路径（穿越提交的轨迹）。
- **黄色圆环**：会话根或压缩标记。
- **白色圆环**：智能体的当前位置。
- **粉色圆环**：选中的提交（点击任意节点）。
- **绿色标签**：分支名称。

当你点击一个节点时，四个按钮会亮起：**移动到提交**、**仓库回退到**、**对话回退到**、**全部回退到**——四个斜杠命令的 GUI 等价物。

右上角图例中的**曲率滑块**控制分支跳转箭头的弯曲程度。

## Pivot 所做的提交

当智能体使用 `GitCommit`（通常通过 `/commit`）时，提交会获得 `Co-Authored-By: Pivot Code` 尾部标记，并被记录在 `state.pivot_commits` 中。GUI 会将这些标记为蓝色。丰富的提交历史：你总是一眼就能分辨哪些提交是你的，哪些是 Pivot 的。

## 何时使用 AGT 而非普通 git

- 普通的 `git` 命令（通过 `Bash`）当然仍然有效。AGT 并不试图取代它们。
- 当**智能体的心智状态很重要**时使用 AGT——走错路之后、重大重构之前、想让智能体从更早的状态继续时。
- 对 Pivot 不需要知道的日常工作使用普通 git。

AGT 关乎**保持智能体对现实的看法与 git 对现实的看法同步**，并让分歧点变得明确。

## 相关

- [reference/slash-commands.md](../reference/slash-commands.md) —— 确切的命令语法。
- [guides/using-the-gui.md](../guides/using-the-gui.md) —— Git 树面板操作指南。
- [concepts/memory.md](memory.md) —— 为什么记忆快照对 AGT 很重要。
- `pivotcode/git_tree/` —— 实现。
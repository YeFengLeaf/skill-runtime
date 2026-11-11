# Skill Runtime

一个面向 Agent Skills 的小型运行时原型：按任务选择少量 skill，给每个 skill 发短租约，并在每轮重新构造请求上下文。它把“调度器不再使用”与“旧正文确实不在下一次请求里”分开报告。

当前处于 **Alpha** 阶段，尚未接入具体模型宿主或真实 tokenizer。路由和 token 估算都应视为可检查的原型行为，不是生产环境的准确性或成本保证。

## 当前能力

- 读取本地 `SKILL.md` 的常见 frontmatter（`name`、`description`、`triggers`、`tags` 等），支持内联列表和缩进的标量列表。路由字段 `triggers` 和 `tags` 遇到不支持的嵌套 YAML 会明确报错；其他嵌套元数据保留为不参与调度的文本。索引阶段计算哈希和大小，但不把 skill 正文注入模型请求；正文只在路由命中、取得租约后才读入请求构建器。
- 确定性的词法路由：返回候选分数、命中理由、估算 token 和 skill 正文预算拒绝结果。目录中大量重复的通用词会降低权重；任务只包含通用词时，明确声明的 trigger 仍可命中。中文二元词只在连续中文片段内生成，不跨标点拼接。没有达到阈值或预算不足时不会把所有 skill 静默装入上下文。
- 同名 skill 全部保留，并按相对目录生成稳定的 catalog key，例如 `review@team-a`。唯一名称仍可直接通过名称查找；重名查找会明确报歧义，需使用 key。路由时按元数据 `name` 去重，同一个名称最多占一个 `max_skills` 槽位。
- 短生命周期租约：按任务切换、路由变化和过期轮次释放，并记录释放原因。
- 三种上下文模式：
  - `ephemeral_system`：每轮由基线消息和当前 skill 重新构造；旧 skill 确实离开新请求时才报告物理移除。
  - `tool_prompt`：只把当前 skill 作为本轮 developer/tool prompt，不写入历史；同样会检查新请求是否不含旧正文。
  - `history_append`：模拟只能追加历史的宿主；会明确返回 `logical_unload_only`，不冒充已经回收旧 token。
- 每次运行返回请求字符数、估算 token、加载/释放 skill 和 SHA-256，便于接入真实宿主后做成本验证。
- `demo` 在相同的两项任务上并排比较每轮重建与追加历史，显示第二轮请求差值和旧正文是否仍在请求中。

## 运行

需要 Python 3.9+，无运行时第三方依赖。

```bash
python -m skill_runtime --skills examples/skills index
python -m skill_runtime --skills examples/skills route "research current facts and cite sources" --skill-budget-tokens 2000
python -m skill_runtime --skills examples/skills run "clean this csv and inspect missing values" --task-id demo --turn 0 --skill-budget-tokens 2000
python -m skill_runtime --skills examples/skills demo
python -m unittest discover -s tests -v
```

也可以安装为命令：

```bash
python -m pip install -e .
skill-runtime --skills examples/skills run "review the code for regressions"
```

## 设计边界

这是 runtime/context adapter，不是 skill marketplace，也不负责下载外部仓库或执行 skill 脚本。默认 `skill_budget_tokens=2000` 是保守示例值，且只限制选中的 skill 正文估算，不限制基线消息、任务文本或包装文本；正文较长的 skill 可能因此没有候选，调用方应按实际上下文容量调整。完整请求 token 数目前是单独估算。两项估算都按约 4 字符一个 token，不等同于特定模型 tokenizer；接入模型 API 时应把 `RequestEnvelope.messages` 交给该 API 的真实 tokenizer 再记录一次。

路由只做词法匹配，不翻译任务或元数据。中文任务不会自动命中只有英文触发词的 skill，反之亦然；需要跨语言召回时，应在 skill 元数据中显式提供对应语言的触发词，或在宿主侧增加翻译/语义检索层。

`history_append` 的行为是有意保守的：管理器可以释放自己的活动集合，但如果宿主已经把正文写进不可变聊天历史，就无法声称这些 token 已被删除。只有每轮重建请求或宿主提供 history compaction 时，`physically_removed` 才有意义。

## MVP 验收对应关系

1. 目录索引不会把正文注入路由上下文；每个记录包含大小和哈希。
2. 路由只返回 top-k 且受预算约束，带理由和拒绝项。
3. 任务切换会生成 `task_switched` 释放事件。
4. 默认模式的下一次请求文本不包含旧 skill 正文，测试直接检查请求体。
5. 追加历史模式返回 `logical_unload_only`，明确暴露宿主限制。
6. 重名目录可以同时索引，key 在目录内稳定且不会混淆租约。
7. 通用词密集目录中，明确的专用能力仍排在前面；路由字段中不支持的 frontmatter 结构会显式失败，非路由嵌套元数据不会阻塞索引。

## 许可证

MIT

## 项目状态

版本、已知限制和开发约定分别见 [CHANGELOG.md](CHANGELOG.md)、[设计边界](#设计边界) 和 [CONTRIBUTING.md](CONTRIBUTING.md)。安全联系渠道将在 GitHub 仓库启用后补充为平台的私密漏洞报告入口。

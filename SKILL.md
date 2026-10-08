---
name: agent-trajectory
description: 把本机 pi 会话日志解析成可视化轨迹、打开回放页，再由大模型解读运行情况（干了什么/哪里卡/哪里烧token）。触发词：轨迹回放、会话回放、看看我的对话运行情况、agent轨迹、trajectory、回溯器。
---

# Agent 轨迹可视化 skill（v1）

目标：帮用户看清**他电脑上**的 pi 对话是怎么运行的。分工铁律——
**确定性工作归脚本，语义判断归大模型**。脚本能跑出来的不进模型嘴里，模型只做脚本做不了的事。

## 四件套（本 skill 目录内，自包含可分发）

| 文件 | 角色 |
|---|---|
| `build_library.py` | 扫 `~/.pi/agent/sessions/` 全量会话 → `lib/`（索引+分片） |
| `parse_pi_session.py` | 单会话 JSONL → 12 动作事件流（boot/user_msg/answer/read/write/exec/web/skill/spawn/report/ask/compress） |
| `agent-trajectory-replay.html` | 回放页：零依赖、零 fetch，file:// 双击可用，无需服务器 |
| `lib/` | 运行时产物（脚本生成，不随分发物走） |

## 固定脚本流程（逐条执行，输出如实转述给用户）

1. **建库**：`python -X utf8 <skill目录>/build_library.py`
   - stdout 的逐对话统计表（事件数/估算token/失败数/写文件数/标题）是第一手原料，直接给用户看
   - 产出 `<skill目录>/lib/index.js` + `lib/c-<id>.js`
2. **打开回放页**：
   - Windows: `start "" "<skill目录>/agent-trajectory-replay.html"`
   - macOS: `open <skill目录>/agent-trajectory-replay.html`；Linux: `xdg-open ...`
   - 左侧选对话，滚轮逐步回放；四区结构=Agent层/资料架/上下文/大模型
3. **实时跟踪**（用户要看某个对话的最新动态）： → 浏览器打开 http://localhost:8765 → 正常载入任一对话 → 点「🔴 跟踪」→ 自动跳到该对话最近一轮，之后新内容写入自动续跟；回看历史即暂停跟随。跟踪=载入后的动作，对当前对话生效；file:// 双击方式只支持回放，跟踪必须走本服务器。
4. **单会话深挖**（用户点名某对话时）：`python -X utf8 <skill目录>/parse_pi_session.py <session.jsonl> <临时目录>/ --split-by-task`
   - 按实质用户消息切成任务段 + manifest.json 统计，供下面的模型解读当原料

## 大模型介入（脚本做不了的语义工作）

- **解读叙事**：用户问"这个会话在干什么/为什么慢"→ 读 `lib/c-*.js` 对应分片（JSON 事件数组），按事件流讲清：用户要什么→agent 动了哪些资料→跑了什么命令→卡在哪
- **诊断维度**（有证据才下结论，逐条引用事件）：
  - exec 占比过高 → 手动泥潭（该写脚本/该用 skill 的在一句句敲）
  - 失败事件聚集 → 读失败 detail 归因（权限/路径/依赖）
  - compress 频繁 → 上下文管理问题（触顶自动 or 手动 compact_context）
  - read 远多于 write → 只读不动手；spawn 多 → 子 agent 派活模式
  - 用户消息重复出现同类问题 → OS/章程缺口，建议改 AGENTS.md
- **跨会话对比**：从 `lib/index.js` 索引找同项目多对话，对比统计表找模式

## 边界（违反即错）

- 解析结果缺事件/动作分类错 → 是 `parse_pi_session.py` 的 `TOOL_MAP`/正则问题，**修脚本**，不要在对话里口头解释圆场
- 会话目录不存在或格式不符（type ≠ session/message/compaction）→ 先 `ls ~/.pi/agent/sessions/` 探明结构再报告，不要硬跑后编造
- `tokens_est` 是字符数×0.7 估算，报告里必须写"估算"
- 子 agent 内部轨迹不展开（子会话文件不跟进）——v1 已知限制，如实告知
- 临时产物（切分的任务 JSONL、报告）放 `_scratch/` 类临时目录，不污染 skill 目录

## 给同事分发

整个 skill 目录打包（zip）发过去即可；同事机器需有 pi 会话日志（`~/.pi/agent/sessions/`）和 python3。首次使用跑一遍"固定脚本流程"步骤 1-2。

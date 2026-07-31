# 杨戬交互体验模拟器修复报告

日期：2026-07-31

## 结论

本轮已将此前“模块存在但运行时未接入”的关键能力接入 Room 主链路。

当前主流程为：

`消息入口 → 恢复用户故事状态 → DIRECT + Guard → Agent Proposal → RESOLVE + Guard → Room 应用裁决 → 旁白描述已确认结果 → 持久化 → 单次发送`

Director 的 `accept / modify / reject` 现在会实际控制用户能看到的输出；非法状态变更和未解锁 Beat 不再只依赖提示词。

## 已修复

### 1. 重启恢复

- 冷启动先加载 Story Plan 和已有 `story_state.json`。
- 已激活故事恢复当前 Beat，不再调用 `reset_state()`。
- 只有全新、无进度的 inactive 状态才从起点启动。
- completed 或已有进度的故事不会被隐式重开。

### 2. Director 和 Room 裁决闭环

- DIRECT 输出通过 `DirectorGuard` 校验；失败自动重试并安全回退。
- RESOLVE 使用稳定的 `proposal_id`，不再使用角色显示名称充当 ID。
- RESOLVE 必须覆盖每个 Proposal。
- `reject` 输出不发布。
- `modify` 的动作使用导演确认后的事实结果。
- `accept` 才能进入最终输出和 NPC 记忆。
- 失败三次时 fail closed：拒绝本回合全部 Proposal，不修改状态。

### 3. 信息和剧情边界

- Director 分发给角色的信息必须来自 Room 的 `allowed_information`。
- 空白名单现在表示“不允许任何信息”，不再跳过校验。
- 输出发送前增加 `forbidden_reveals / must_not` 确定性过滤。
- `next_beat` 必须属于当前 Beat 的可用 Transition。
- 状态键只允许 `weather / mood / world_day / item_* / reveal_* / character_*`。
- 旧版 Director 直接修改状态的模式默认关闭；只有显式设置
  `YANGJIAN_ALLOW_LEGACY_MODE=1` 才允许启用。

### 4. 世界状态提交

- 修复 `state_manager.apply_changes()` 返回值未赋回 `state` 的问题。
- 物品位置、角色状态和已揭露信息分别进入统一事实表。
- Recovery Arc 增加节点数量、唯一 ID 和 Transition 目标校验。

### 5. 杨戬 Agent

- 修复 Room 传入扁平 `goals`、Agent 按嵌套字典读取导致的 TypeError。
- 同时兼容扁平目标和旧版嵌套目标。
- 提示词要求的 `「动作」` 现在会正确解析为动作，不再误作对白。

### 6. 旁白 Agent

- 旁白移到 RESOLVE 之后执行。
- 只接收导演已经确认发生的结果。
- 注入与杨戬相同的公共事实摘要。
- 最大长度从“两倍后截断”改为严格 `max_chars`。
- 旁白输出同样经过禁止信息过滤。

### 7. NPC Manager

- 修复 `DirectorTask` 缺少必填字段。
- 修复 `build_turn_context()` 参数不匹配。
- 修复 `validate_proposal()` 参数顺序和结果属性错误。
- 修复生命周期 wrapper 参数不完整。
- 修复动态上下文被二次 JSON 编码。
- 接入 `proactive` 字段和 Agent 级 Langfuse ID。
- 新增 LLM NPCProfile Generator，失败时使用安全确定性档案。
- 当前 Beat 的 NPCRequirement 会由 Room 自动 acquire/activate。
- 只有 Director 接受或修改后的 NPC 事件才写入 NPCMemory。
- 新增原子 JSON NPC Repository，重启后身份和记忆可恢复。

### 8. 多用户、线程和并发

- 新增 `runtime_context.py`。
- `world_state`、`story_state`、`world_facts`、NPC 记录和偏好信号按
  `user_id / thread_id` 分目录持久化。
- 默认用户继续使用原有文件，保持向后兼容。
- Room Tick 使用进程内 `RLock`。
- 新增跨进程文件锁，避免 Gateway、Poller 或 Cron 同时写同一状态。

### 9. 消息入口

- Photon Bridge 同时支持脚本导入和包导入。
- 新增 `handle_and_deliver()`：一条入站消息只处理一次、发送一次。
- stdin 和 iMessage Poller 统一使用该入口。
- 修复 Story Plan 路径推导错误，统一使用 `DEFAULT_PLAN_PATH`。

### 10. 可观测性

- Room 的 Langfuse Context 通过 `ContextVar` 传给所有 LLM 调用。
- 同一 Tick 的 Director、杨戬、旁白和 NPC 使用相同 session_id。

### 11. 偏好和可迁移性

- 仅采集用户明确表达的偏好，不推断未表达偏好。
- 偏好信号记录真实 `user_id`，并按用户隔离。
- 移除源码中的个人绝对路径。
- 项目目录默认从源码位置推导，也可通过
  `YANGJIAN_PROJECT_DIR` 显式设置。

## 测试

验证结果：

- Room / Director Guard / NPC Manager / Runtime：39 项通过。
- Story Generator：9 项通过。
- 总计：48 项通过，0 失败。
- `compileall`：通过。
- `git diff --check`：通过。
- Photon Bridge 脚本导入：通过。
- Photon Bridge 包导入：通过。

新增覆盖：

- 冷启动恢复、全新启动、completed 不重开。
- Director 信息越权。
- 未解锁 Beat。
- accept / modify / reject 实际控制输出。
- 禁止信息发送前过滤。
- 杨戬扁平目标和动作解析。
- NPC Runtime 真实接口。
- NPC JSON 持久化。
- 旁白公共事实注入。
- 多用户状态隔离。
- 明确偏好按用户记录。

## 部署要求

1. 部署前备份：
   - `world_state.json`
   - `story_state.json`
   - `world_facts.json`
   - `contexts/`
2. 推荐设置：

   ```bash
   export YANGJIAN_PROJECT_DIR="/Users/xiaoxianhan/Documents/yangjian-room"
   ```

3. Hermes/Photon 入站路由应调用：

   ```python
   photon_room_bridge.handle_and_deliver(message)
   ```

   不要再分别调用 `handle_message()` 和 `deliver_outputs()`，否则可能重复发送。

4. 不要设置 `YANGJIAN_ALLOW_LEGACY_MODE=1`，除非确实需要运行旧版 Markdown Phase。
5. 重启 Hermes Gateway 和 Photon Poller 后执行一次真实 iMessage 烟雾测试。

## 尚需部署环境确认

脱敏源码中没有真实凭证和线上 Gateway 配置，因此以下项目无法在本机完成：

- 真实 DeepSeek 输出质量与延迟。
- Photon 入站 webhook/AGENTS 路由是否确实调用新入口。
- 真实 iMessage 是否重复发送或漏发。
- Langfuse Dashboard 中 session 分组是否符合预期。
- macOS 上 Gateway、Poller、Cron 的实际进程组合。

这些属于部署验证，不是当前源码中仍未实现的逻辑。

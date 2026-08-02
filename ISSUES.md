# 杨戬项目 Issue 文档

记录细节逻辑的修改决策与原因。Q&A 格式。

---

## Q: recovery 退出时是否应该调 RESOLVE 判定 beat goal？

**A: 分两种情况：**

### 情况1：recovery 子目标达成退出 -> 调 RESOLVE

recovery 子目标达成，说明用户在 recovery 期间做了期望行为。但 beat goal 和 recovery 子目标是不同的东西，子目标达成不代表 beat goal 达成。调 RESOLVE 判定 beat goal 有意义：
- `goal_met=true` -> 走 transitions 推进
- `goal_met=false` -> 走 rejoin_target 推进

**实现**：`_resolve_for_goal_judgment()` 用当前 tick 的 actor_results 调一次 `director.handle_resolve`，取 `goal_met` 字段。

### 情况2：recovery 超时强制退出 -> 不调 RESOLVE

recovery 子目标都没达成，beat goal 更不可能达成。调 RESOLVE 几乎不可能返回 `goal_met=true`，浪费一次 LLM 调用。直接 `advance_beat(rejoin_target)` 推进。

---

## Q: recovery 是 beat 还是 beat 内的支线？

**A: recovery 是 beat 内的支线，不是单独的 beat。**

- recovery 不改变 `current_beat_id`
- recovery 不重置 `beat_tick_counter`
- recovery 有独立的 `recovery_tick_counter`，退出时重置
- `get_current_beat_info` 在 recovery 时返回当前 beat 的信息 + recovery 上下文叠加
- `_trigger_recovery` 不覆盖 `beat_goal` / `beat_max_turns`

---

## Q: beat 达到 max_turns 时如何处理？

**A: room 自己判定，强制走 RESOLVE。**

- room 在 RESOLVE 前检查 `beat_tick_counter >= beat_max_turns`
- 如果达到，强制 `resolve_required=True`（即使 director 走 fast path 也强制走 RESOLVE）
- director DIRECT 阶段不感知 max_turns，正常工作
- 不在 DIRECT 阶段注入 max_turns_warning 到 director 上下文

---

## Q: 不触发 goal 判定的 tick 是否需要输出 goal_met？

**A: 不需要。** 只有走 RESOLVE 的 tick 才输出 goal_met。fast path（resolve_required=false）的 tick 不输出 goal_met，也不需要。

---

## Q: rejoin_target 算错--为什么找下一个 beat 会找成 m2？

**A: `_trigger_recovery` 中当前 beat 是最后一个主线 beat 时找不到下一个，fallback 到 `beats[0]`=m1，导致故事倒退。**

修复：最后一个 beat 触发 recovery 时，有结局则直接推进到结局，没结局则标记 `status=completed`。不再 fallback 到 `beats[0]`。

---

## Q: 更新 current beat 时如果 target 不在 story plan 中怎么办？

**A: 判定为故事线已完成，进入自由聊天模式。**

- `advance_beat()` 中先 `_find_beat(plan, target_beat_id)` 校验
- 不存在则标记 `status=completed`
- `_tick_two_stage` 中 `status=completed` 时走 `_tick_free_chat()`（只调杨戬，不走 beat 推进/RESOLVE）

---

## Q: 为什么没触发过支线剧情？

**A: `check_and_unlock_side_arcs` 调用位置不完整。**

三个原因：
1. 只在 `next_beat` 推进后调用，`goal_met` 推进后没调用
2. 没有每回合调用，可能遗漏解锁时机
3. `selected_side_arc` 字段在 schema 中标注 "never used"

修复：
- `_tick_two_stage` Phase 0 每回合开头调用 `check_and_unlock_side_arcs`
- `goal_met` 推进后也调用

---

## Q: scene.location 为什么一直是空的？

**A: 提示词写了但失效。**

失效原因：
1. narrator 硬性规则中没有 location 要求，location 指令只在 INPUT_TEMPLATE 末尾容易被忽略
2. director 的 scene_update 规则不强制每回合检查 location
3. 当 director 没要求旁白时 narrator 不被调用，location 回写路径不走

修复：
- narrator SYSTEM_PROMPT 硬性规则加第12条：每次旁白必须填 location
- director scene_update 规则加：当前 location 为空时必须填
- room.py 中 location 回写增加 `!= "null"` 过滤

---

## Q: trace 开始/结束时需要记录什么？

**A: 全量 story state + recovery 状态快照。**

- `room.trace_start`：tick 开始时记录
- `room.trace_end`：tick 结束时记录
- 字段：beat_id, completed_beats, beat_goal, beat_goal_met, beat_tick_counter, in_recovery, recovery_arc_id, recovery_rejoin_target, recovery_sub_goal, recovery_tick_counter, recovery_max_turns, main_progress, unlocked_side_arcs, scene_location, relationship

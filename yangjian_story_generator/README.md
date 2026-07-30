# 杨戬 Story Generator

## 当前定位

这是一个准备迁移进杨戬主项目的独立 Python 包，负责定义、解析和静态校验私有故事计划。

当前版本只实现不依赖主项目的核心逻辑。它不会自行调用真实模型，也不保存 Room 运行进度。

- Python：`>=3.11,<3.14`（与 Hermes 当前约束一致）
- 第三方运行依赖：无
- 当前开发机验证版本：Python 3.13
- 测试：9 项通过

## 已开发内容

### 输入结构

- `CharacterContext`：杨戬角色理解输入。其内容应来自杨戬的真实故事、既定经历和 SOUL，而不是普通运行时 AI 对话。
- `PreferenceSnapshot`：Hermes 导出的版本化用户偏好快照。
- `StoryBrief`：本次生成请求的主题和附加约束。
- `StoryStandard`：主线、副线和结局数量等确定性规则。

### Story Plan

已定义：

- 主线、主线里程碑和最多两个主线结局。
- 副线、多个副线结局及主线进度/里程碑/旗标解锁条件。
- Story Beat、分支条件、分支汇合及必须保留的后果。
- NPCRequirement。
- 秘密、伏笔、允许信息和禁止提前透露的信息。

Story Plan 是故事生成器的一次完整私有输出，不包含预写对白，也不包含 Room 的运行时状态。

### 静态校验

`StoryPlanValidator` 已实现：

- 主线结局必须为 1～2 个。
- 副线结局数量默认最多 4 个，可配置。
- 主线只允许用户和杨戬作为核心参与者。
- 副线必须包含用户和杨戬，并声明对主线的影响。
- 重复 ID、未知引用、不可达节点和无结局路径检查。
- 非法循环检查，当前默认不允许故事图循环。
- 副线解锁里程碑检查。
- NPCRequirement 引用和知识冲突检查。
- 伏笔节点、秘密受众及信息边界冲突检查。

### 编解码和防剧透

- 完整私有 Story Plan 的 JSON 编解码。
- `story_plan_public_summary()`：只返回非剧透元数据。
- `StoryGenerator` / `AsyncStoryGenerator`：支持 Hermes CLI 同步路径和 Gateway 异步路径。

## 尚未开发

以下内容依赖杨戬主项目，目前只保留接口或 TODO：

- 真实模型 SDK 适配器。
- 故事生成提示词的实际调试。
- 从杨戬真实故事和 SOUL 生成 `CharacterContext` 的模型流程。
- Hermes 偏好档案到 `PreferenceSnapshot` 的转换。
- Story Plan 数据库或私有文件存储。
- Validator 失败后的局部节点重写。
- 用户偏离后的短回归剧情生成。
- Room、Director 和 NPC Manager 集成。
- 用户自然语言到分支意图的判断。
- 运行状态、分支选择、进度和重启恢复；这些应由 Room 负责。

具体待办也记录在 `INTEGRATION_TASKS.md`。

## 目录说明

```text
src/yangjian_story_generator/
  models.py       数据结构
  codec.py        私有JSON编解码和公开摘要
  validation.py   确定性故事图校验
  generator.py    模型无关的生成编排
  ports.py        模型、存储和资料加载接口
tests/
  test_story_core.py
```

## 迁移到主项目的建议顺序

1. 确认主项目 Python 版本、同步/异步模式、模型 SDK、数据模型方案和测试框架。
2. 将本包作为子包复制进主项目，或通过本地包方式安装。
3. 先运行现有测试，确认 dataclass 和 `StrEnum` 与主项目兼容。
4. 实现 `CanonicalSourceLoader`，连接杨戬真实故事与 SOUL。
5. 实现 Hermes 偏好导出，生成 `PreferenceSnapshot`。
6. 实现 `StructuredModelClient`，要求模型输出符合 Story Plan 结构。
7. 实现 `StoryPlanRepository`，完整计划必须使用私有存储。
8. 将 Story Plan 交给 Room；Room 管理进度，Director 只消费 Room 提供的当前可用节点。
9. 对接 NPC Manager，仅传递具体 NPCRequirement，禁止 NPC 读取完整 Story Plan。
10. 使用主项目实际 Python 版本运行本包测试和端到端测试。

## 与 NPC Manager 集成时的注意事项

Story Generator 和 NPC Manager 的 `NPCRequirement` 字段已经对齐，但仍是两份重复定义。迁移时应：

- 优先把它们合并到主项目共享的 domain models 中；或
- 编写显式转换函数，不要通过隐式字典传递。

Story Generator 只负责描述“剧情需要什么 NPC”，不能创建 NPC Agent、保存 NPC 记忆或管理生命周期。

## 安全与职责边界

- 完整 Story Plan 不得进入用户 prompt、普通日志或公开 API。
- 用户侧只能使用 `story_plan_public_summary()` 或主项目另行定义的安全投影。
- Story Generator 不得直接修改 Room 状态。
- `CharacterContext` 不得自动吸收普通 AI 对话作为杨戬权威人格来源。
- 模型输出必须先解析并通过 Validator，不能直接交给 Director。

## 使用示例

真实模型接入时实现 `StructuredModelClient`：

```python
from yangjian_story_generator import StoryGenerator

generator = StoryGenerator(project_model_adapter)
plan = generator.generate(
    character=character_context,
    preferences=preference_snapshot,
    brief=story_brief,
)
```

模型结果解析或校验失败时不得激活故事。

## 本地测试

```powershell
cd C:\Users\T617525P\Downloads\yangjian-story-generator
$env:PYTHONPATH="src"
py -3.11 -m unittest discover -s tests -v
```

当前开发机只有 Python 3.13，已在 3.13 上通过测试。迁移后需使用主项目实际 Python 版本再验证。

Hermes 自动接管和剩余兼容工作的精确清单见 `HERMES_HANDOFF.md`。

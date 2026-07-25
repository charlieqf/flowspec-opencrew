# Koubo StoryBoard Dialogue Key 统一资源绑定需求 —— 审核结果

被审文档：`docs/Koubo_Storyboard_DialogueKey_统一资源绑定需求与测试案例.md`（v0.1）

审核日期：2026-06-24

审核方式：文档逐节核对 + 对照真实仓库代码逐一验证（前端 `frontend/src/modules/koubo/`、后端 `backend/opcrew_backend/koubo/koubo_storyboard/`、`ToolLibrary/Analysis_V1/`）。本文所有 file:line 均经代码核实。

---

## 0. 总评

需求方向正确：统一 `dialogue_asset_key` 作为唯一资源锚点、禁止运行时 fallback、引入 Plan stale 机制、最小回归测试集覆盖面好，应继续推进。

但**不能按现稿直接实施**，两类问题必须先修：

1. **§7「当前代码梳理」与真实代码大面积对不上** —— 多个声称要"移除"的符号（`manualDialogueAssetKey`、`newManualDialogueFields`、`manual_dialogue_asset_key`、`dialogue_lookup_keys` 等）在仓库里**不存在**，病因诊断也错了。按现稿实现会扑空。
2. **三条主链路问题被遗漏或与现状冲突**：`dialogue_id` 稳定契约被现有代码违反、`segment.dialogue_ids` 消费者清单不全、stale 签名与已存在的一整套 signature 体系未对齐。

下面按严重度排列，🔴 必须先解决，🟠 实现前需决策，🟡 次要。

---

## 0.5 根因与方案达成度（先读这一节）

在逐条 findings 之前，先给一个顶层判断：**这份需求要解决什么、它的方案能不能达成。** 下面四个根因缺口与后续详细条目一一对应（已标注 §X）。

### 它真正要解决的问题

§1 列了一堆症状（manual key、串绑、反查误命中、新增 Dialogue 不进计划……），但根因只有一个：**资源绑定锚点不是一个稳定且唯一的"身份"。**

具体到代码，当前绑定锚点实际上**等于 `dialogue_id`**——所有消费端都通过 fallback 链（`kouboStoryboardTts.js:485`、各 `dialogue_match_keys`、`05_01 dialogue_key()` :196）从 `dialogue_id`/`srt_id` 派生 key；而 `dialogue_id` 偏偏是**位置型、且每次重排/Split 被重算**（`renumberPlan` :63、`recalculate` :330）。于是绑定跟的是"第几个"，不是"哪一个"：重排/Split 改 `dialogue_id` → 锚点漂移 → 素材跟位置走；新增 Dialogue clone 继承左邻 key（:1082）→ 重复/空 key → fallback 误命中邻居。

### 方案在概念上是对的

"唯一稳定的 `dialogue_asset_key` + 禁止 fallback + 结构变化让 Plan stale"——正是把绑定从"位置"切换到"身份"的正确形状。若**完全落地**，确实能消除误绑。战略方向不推翻。

### 但作为"能否达到目的"，现稿是 No，有四个缺口

**1. 没有锁住整个方案唯一的承重前提。** 方案成立的前提是：每条 Dialogue 在任何时刻都有非空、不可变的 `dialogue_asset_key`。只要有一条路径让它为空（如 clone 新增），就立刻退回老的 fallback 失败模式。文档只在 §7.2 一行里轻描淡写"缺失由加载规范化补齐"，没把它提升为**中心不变量并强制 enforce**；同时把真正的破坏源（位置型 `dialogue_id` 重算 + 被当 fallback 用）留在改造范围之外。→ 详见 **§1、§2、§6.5**。

**2. 分层 key 方案是为目标过度设计的，残余风险几乎都在这里。** "稳定唯一锚点"根本不需要 `srt_0004_01_01` 这种带血缘的可读 key——文档给 `dialogue_id` 用的 `dlg_a7c91e` 这类稳定不透明 ID 同样满足唯一+稳定+无排序语义，却**没有**父级派生、两位序号上限、首条前插入特例、lineage-与-视觉顺序反序、跨 Scene 派生这一堆边界。可读血缘是个 *想要*，不是 *需要*；用不透明 key 做绑定、把可读 lineage 降级成纯展示字段，能用更少风险拿到同样正确性。→ 详见 **§7 开放决策、§8**。

**3. Segment 与 Dialogue 视频路径共用，未闭合反查歧义。** 这一条经核实是**当前一致的有意约定**（需求 doc:309 + 代码 `05_02:2601` 都把 Segment 视频绑回代表 Dialogue），不是已证 bug。但 §1 抱怨的反查路径 `{asset_key}_Video_Raw.*` 在"代表 Dialogue 同时也有 Dialogue 级视频"时仍无法区分二者——这个歧义需要明确表态而非留白。→ 详见 **§4（🟠）**。

**4. stale 机制把"误绑"换成了"动不动全量重生成"。** 现稿是整板单签名（含 text/duration）：加一条 Dialogue、甚至改个字 → 三类 Plan 全 stale → 用户必须重新生成。这对"绝不误绑"是对的、安全的；但 §1 的另一半诉求是"新增 Dialogue 能顺利进计划执行"。用全局粗签名换来的是增量编辑被频繁打断——若目标只是"永不误绑"则达成，若目标含"制作中途平滑增删 Dialogue"则部分自我抵消。文档没承认这个权衡。→ 详见 **§5、§7**。

### 结论

方向对；但按现稿不能完全达成目的。它把力气花在"清理一套并不存在的 manual 代码"和"设计一套漂亮但非必需的分层 key"上，却没锁死真正的承重前提、没改真正的破坏源、没正视 staleness 的 UX 代价。要让它真正达成目的，按重要性收敛为四件事：(1) 把"`dialogue_asset_key` 恒非空且不可变"作为中心不变量在 load/save 强制，同时停掉 `dialogue_id` 位置型重算；(2) 认真考虑用稳定不透明 key 做绑定、可读 lineage 降为展示字段；(3) 给 Segment 路径共用明确表态；(4) 按"是否需要增量编辑"重新定 stale 粒度。

---

## 1. 🔴 §7.1「manual key 生成代码」与仓库不一致 —— 病因诊断错误

§7.1（doc:350-353）声称存在以下需要"移除"的代码，核实结论：

| 文档声称（doc:350-353） | 真实情况 |
| --- | --- |
| `kouboStoryboardModel.js` 有 `manualDialogueAssetKey` / `newManualDialogueFields` | **不存在** |
| `KouboStoryBoardModule.jsx addDialogueAfter` 调用 `newManualDialogueFields` | **否**，见下 |
| `kouboAgentChat.js add_dialogue_after` 调用 `newManualDialogueFields` | **否**（`applyStoryboardEditCandidate` :255-271 直接建字段） |
| `storyboard_plan_services.py` 有 `manual_dialogue_asset_key` 生成 `_manual` | **不存在**（`dialogue_asset_key()` 实际在 `asset_core_services.py:129-138`，从 srt_id/dialogue_id 派生，无 `_manual`、无重复处理） |
| `emptyDialogueWorkingAssets`（§7.3 doc:373） | **不存在**，实际是 `kouboStoryboardTts.js:527` 的 `ensureDialogueWorkingAssets` |

**真实病因**（`KouboStoryBoardModule.jsx:1082`）：

```js
const next = { ...copy(scene.dialogues[index]), dialogue_id: `${scene.scene_id}_dialogue_new_${Date.now()}`, text: "", duration: 0, source_image_paths: [], image_path: "", bound_image_path: "" };
```

`addDialogueAfter` **clone 左邻 Dialogue**，显式清空了 `image_path / bound_image_path / source_image_paths`，**却没有覆盖 `dialogue_asset_key`** → 通过 spread **继承左邻的 key，产生重复 key**（若左邻 key 为空则得到空 key 走下游 fallback）。代码里不存在任何产出 `_manual` 字符串的逻辑，连症状 key 格式都是 `_dialogue_new_` 而非文档反复引用的 `_manual_`。

**整改建议**：§7.1 整段重写，把目标从"移除 manual 生成代码"改为：

> 新增 Dialogue（UI `addDialogueAfter` 与 Agent `add_dialogue_after`）**禁止 clone 继承资源 key**，必须调用统一的确定性 `dialogue_asset_key` 生成函数；新增对象初始化为空素材。

---

## 2. 🔴 `dialogue_id` 稳定契约被现有代码违反，且未进改造范围/测试

文档 doc:116 要求 `dialogue_id` 保持稳定、不因重排重算。但前后端**都按位置重算它**：

```js
// frontend kouboStoryboardModel.js:63-64 (renumberPlan)
dialogue.dialogue_id = `${scene.scene_id}_dialogue_${String(dialogueIndex + 1).padStart(3, "0")}`;
```
```python
# backend storyboard_plan_services.py:330 (recalculate)
dialogue["dialogue_id"] = f"{scene_id}_dialogue_{dialogue_index:03d}"
```

这违反契约，并且是**主链路风险来源之一**：当前 asset-key 的 fallback 正是从 `dialogue_id` 派生（`kouboStoryboardTts.js:485`、各后端 `dialogue_match_keys`），而 `dialogue_id` 是位置型且每次重排/Split 被重写。需要注意限定：它**只在 `dialogue_asset_key` 缺失、或运行时 fallback 生效时**才直接放大"重排 / Split 后素材改绑"；有显式且稳定 `dialogue_asset_key` 的路径不一定受它直接影响。因此它是"会放大 fallback 误绑"的隐患，而非每条路径的直接成因——但只要还存在 fallback，就必须连它一起修。

**整改建议**：
- §7.x 明确把 `renumberPlan`（FE）与 `recalculate`（BE）列入必须修改：重排/Split 只更新数组位置与 `dialogue_index`，**不重算 `dialogue_id`**。
- 测试补：KEY-03 / SCENE-01 / SHOT-01 增加断言"保存 / 刷新 / Split 后 `dialogue_id` 不变"。

---

## 3. 🔴 `segment.dialogue_ids` 改 asset key 的消费者清单不完整

doc:306 要求 `segment.dialogue_ids` 的值必须是 `dialogue_asset_key`。但 §7 漏掉了两个关键文件，其中一个还是 `dialogue_ids` 的**产出端**：

```python
# 05_01_VideoPlanGenerator.py:196-197  —— 产出 key 的源头
def dialogue_key(dialogue, index):
    return sanitize_asset_key(text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id")), ...)
# 05_01:848  —— dialogue_ids 本身就是用 srt_id/dialogue_id 拼出来的
dialogue_ids = [text_value(item.get("srt_id") or item.get("dialogue_id")) for item in dialogues[...]]
```
```python
# 06_01_VideoPlanComposer.py:352-354  —— 消费端用 srt_id/dialogue_id 建索引
dialogue_index = { text_value(item.get("srt_id") or item.get("dialogue_id")): item for item in scene_dialogues(...) }
# :360/:364  用 segment.dialogue_ids 反查该索引
```

若只把 `dialogue_ids` 的值改成 asset_key，而产出端（05_01）与消费端索引（06_01）仍按 srt_id/dialogue_id，**Plan 生成→执行→字幕→提示词链路会断**。

还有一个**间接消费枢纽**漏了：`05_02_VideoPlanExecutor.py:1519` 的 `flatten_dialogues()`，它把 storyboard 建索引时**仍按 `srt_id` / `dialogue_id`** 建 key：

```python
# 05_02:1530
key = text_value(dialogue.get("srt_id") or dialogue.get("dialogue_id"))
```

而下游一批模块都靠它 + `segment.dialogue_ids` 反查文本/回绑：
- `05_04_ImagePlanExecutor.py:516`：`VPE.flatten_dialogues(storyboard)` → `bind_segment_output_to_storyboard`（图片回绑）。
- `video_plan_executor_modules/video_gpt.py:169` `prompt_text_from_dialogues(segment, dialogue_index)`：用 `segment.dialogue_ids` 从该索引取每条 Dialogue 文本拼 prompt。

只要 `flatten_dialogues()` 的索引 key 还是 srt_id/dialogue_id，而 `dialogue_ids` 改成了 asset_key，**反查直接 miss → 图片回绑失败、视频 prompt 取不到台词文本**。

**整改建议**：全量审计 `segment.get("dialogue_ids")`、`dialogue_key()`、`flatten_dialogues()` 及其索引构造，连产出端一起改锚 `dialogue_asset_key`。`05_01:196/848`、`06_01:352`、`05_02:1519 flatten_dialogues()` 及其消费者（`05_04:516`、`video_gpt.py:169`）都必须进 §7.2 清单。

---

## 4. 🟠 Segment 级与 Dialogue 级视频路径共用 —— 必须澄清是否有意复用

§4 同时定义：
- Dialogue 新视频 = `{dialogue_asset_key}_Video_Raw.*`（doc:262）
- Segment Raw Video = `{segment.asset_key}_Video_Raw.*`（doc:270），且 `segment.asset_key = dialogue_ids[0]`（doc:274）

当 Segment 代表 Dialogue 即该条 Dialogue 时，两者解析到**同一路径**（§5 示例自证 `srt_0004_Video_Raw.mp4`，doc:298）。

**注意：这很可能是有意设计，而非已证明的 bug。** 原需求 doc:309 明确写了"Segment 跨多条 Dialogue 时，视频仍绑定到代表 key"；代码也确实把 Segment final 绑回首条 Dialogue（`05_02_VideoPlanExecutor.py:2601`，`bind_segment_output_to_storyboard` 取 `dialogue_ids[0]`）。所以"代表 Dialogue 视频路径 == Segment 视频路径"是当前一致的约定，不应判定为串绑。

但需澄清一个真实风险：背景 §1 依赖 `{asset_key}_Video_Raw.*` 反查状态，在"代表 Dialogue 单独也有 Dialogue 级视频"的场景下，反查无法区分这是 Dialogue 级还是 Segment 级产物。

**整改建议**：明确表态二选一——(a) 确认"代表 Dialogue 视频与 Segment 视频有意复用同一产物"，并在 §4/§5 写死这一约定；或 (b) 若两者需独立存在，给 Segment 级产物独立后缀（如 `{asset_key}_Segment_Video_Raw.*`）。不要停在模糊状态。

---

## 5. 🟠 Plan stale 签名与已存在的 signature 体系未对齐

§6 新造 `dialogue_key_signature` / `generated_from_storyboard_revision`（doc:316-334），但仓库已有一整套签名机制：

```python
# video_plan_signature_services.py:292-301
return {
  "scope_signature": ..., "parameter_signature": ...,
  "storyboard_structure_signature": structure_signature,
  "media_binding_signature": media_binding_signature,
  "consistency_reference_signature": ..., "input_signature": ...,
}
```
另有 `plan_hash`、`source_plan_hash`、`source_storyboard_sha256`。且 snapshot **已包含 `dialogue_asset_key`**（:228，进入 `structure_signature`）。

**两个关键事实**：
1. 文档新字段与现有体系重复，必须说清是**扩展还是替换**，否则会出现两套互不感知的 stale 判断。
2. `media_binding_signature`（:266-291）**只含 `dialogue_id` 和图片路径，不含 `dialogue_asset_key`** —— 即"素材绑定 stale"判断目前仍锚在 `dialogue_id` 上。§6 真正要做的不只是加字段，而是把 media binding 的锚点从 `dialogue_id` 换成 `dialogue_asset_key`。

**整改建议**：§6 改为复用并扩展现有 `video_plan_signature_services` 体系；明确 (a) 新结构签名锚定 `dialogue_asset_key`；(b) `media_binding_signature` 改锚点；(c) 文本 / 时长变化是否也算 stale（现稿 hash 含 text/duration，需与"结构变化才 stale"的措辞统一）。

---

## 6. 🟠 §7.2 多 key fallback 清单：大体准确，两行需修正

核实可放心实现的（与现状一致 ✓）：
- `image_plan_routes.py dialogue_match_keys`（:69-83，多 key + index suffix ✓）
- `video_plan_artifact_services.py dialogue_match_keys`（:71-85，:106 用于 **final bound 状态命中**判断 ✓；注：这是判断 artifact 是否已绑定，不是 confirm-final 写回——写回见 §7.2 补充点名的 video-only 端点）
- `video_only_plan_routes.py dialogue_match_keys`（:118-132 ✓）
- `kouboStoryboardTts.js dialogueAssetKey()` fallback（:485 ✓）
- `05_03_ImagePlanGenerator.py:276`（`first_dialogue_id(...) or asset_key or segment_id` 一字不差 ✓）
- `05_02_VideoPlanExecutor.py`（`dialogue_match_keys` :1564 + `segment_asset_key` :1691 ✓）
- `05_06_VideoOnlyPlanExecutor.py:255`（复用 VPE ✓）

需修正两行：
- `video_plan_load_services.py` 的 `dialogue_lookup_keys` / `existing_working_slot_for_keys`：**这两个函数名不存在**。实际链路是 `video_plan_load_services.py:71` 先 `asset_key = dialogue_asset_key(dialogue)`（**fallback 就发生在这一步**，见下方 🔴 核心 helper），再用 `working_asset_services.py:62 existing_working_slot_path()` 按 `asset_key+slot` 查文件。所以"收敛"的落点不在 load services，而在它调用的 `asset_core_services.dialogue_asset_key()`。
- `05_05_VideoOnlyPlanGenerator.py` 的 `actual_dialogues_by_asset`（:226）实际是**单 key**（走 `VPG.dialogue_key()`），并非"多 key 建索引"；其真正的 fallback 在任务构造处的 `segment_id`（:274）。

### 6.1 🔴 §7.2 漏了后端"运行时 fallback 总闸"——`asset_core_services.dialogue_asset_key()`

§7.2 逐文件收敛各处的 `dialogue_match_keys`，却漏了它们脚下的总闸。后端绝大多数路径取 key 都走同一个 helper：

```python
# asset_core_services.py:129-138
def dialogue_asset_key(dialogue):
    explicit = text(dialogue.get("dialogue_asset_key"))
    if explicit:
        return explicit
    srt_id = ...            # 缺失则 fallback 到 srt_id
    key = re.sub(..., srt_id or text(dialogue.get("dialogue_id"))).strip("_")   # 再 fallback 到 dialogue_id
    return key or "dialogue"
```

它被运行时高频调用，覆盖三类关键路径：
- **recalculate 写回**：`storyboard_plan_services.py:335` `dialogue["dialogue_asset_key"] = dialogue_asset_key(dialogue)` —— 等于把 fallback 出来的 key **固化进数据**。
- **asset bind 取文件名前缀**：`asset_reference_services.py:338/393/397/421`。
- **video slot / Working 文件加载**：`video_plan_load_services.py:71`。

**结论**：只要这个 helper 还 fallback 到 `srt_id`/`dialogue_id`，§7.2 把各处 `dialogue_match_keys` 改成精确匹配也**没真正堵住运行时 fallback**——总闸还开着，而且 `:335` 还会把 fallback 结果写回数据，长期污染。

**整改建议**：把它拆成两个职责清晰的函数——
1. `derive_dialogue_asset_key(dialogue)`：**仅供迁移 / 加载规范化**，允许从 srt_id/dialogue_id 推导补齐（即 §0.5 那条"承重前提"的 enforce 点）。
2. `dialogue_asset_key(dialogue)`：**运行时只读显式 `dialogue_asset_key`**，缺失即报错/blocked，不再 fallback。
并审查上面三类调用点分别该用哪一个（`:335` 应改用 (1) 且只在规范化阶段执行）。

补充（confirm-final 反写）：§7.2（doc:360）**已在文件级覆盖**到 `video_plan_artifact_services.py` 的 final 绑定判断与 `video_only_plan_routes.py` 的多 key fallback，不算遗漏。但应**显式点名 video-only 的 confirm-final 端点**：`video_only_plan_routes.py:391` 的 `bind_video_in_storyboard_payload()` 用 `dialogue_match_keys` 反写最终视频（由 `/api/koubo-storyboard/tasks/{task_id}/video-only-plan/segments/{asset_key}/confirm-final` 调用），BIND-02 正测这条路径，整改时务必点到该函数。

---

## 6.5. 🟠 对象定位 vs 资源绑定 —— 不要误删 `dialogue_id` 操作入口

这是被审文档与本审核都没显式写出的一条实现边界，容易在"全面禁用 `dialogue_id`"时误伤。

`dialogue_id` 该禁的是**资源绑定**用途，不该禁的是**编辑对象定位**用途。当前接口正是按后者设计：

```python
# asset_routes.py:1749  —— /asset-bind、/asset-clear 收 dialogue_id 定位对象
dialogue_id = text(payload.get("dialogue_id"))
```
```python
# asset_history_services.py:336  —— find_dialogue 按 dialogue_id 找编辑对象
def find_dialogue(plan, dialogue_id):
    ...
    if text(dialogue.get("dialogue_id")) == dialogue_id:
        return shot, scene, dialogue
```

**正确边界**：`/asset-bind`、`/asset-clear` 这类接口**可以继续用 `dialogue_id` 定位"要操作哪个 Dialogue 对象"**；但定位到对象后，**Working 文件名与资源归属必须改用该对象的 `dialogue_asset_key`**。

**整改建议**：在被审文档新增一节"对象定位 vs 资源绑定"，明确：
- 编辑入口（选中/绑定/清除/合并/Agent 指令）继续用 `dialogue_id` 定位对象——**不要删除这些入口**。
- 一旦进入文件路径 / Plan asset / 反查环节，一律切到 `dialogue_asset_key`。
- 配合 §2 已停掉 `dialogue_id` 的位置型重算（否则用 `dialogue_id` 定位对象本身也不稳）。

---

## 7. 🟠 实现前必须拍板的开放决策

| 项 | 现状 | 建议 |
| --- | --- | --- |
| 首条前 / 空 Scene / 跨 Scene·Shot 首位插入的 key（doc:245-246，§3.3.5 仍是 TODO） | 文档自留"建议 `srt_0000_01` 或 UI 禁止" | 直接拍板。`srt_0000_01` 会与真实 `srt_0000` 撞且语义误导；建议 UI 禁止首条前插入，或用不会撞 SRT 的保留前缀（如 `head_01`）。**新增 KEY-00** 覆盖这些场景。 |
| VideoPrompt 归属（doc:262 列 Dialogue 级，doc:297 示例放 segment `planned_outputs.video_prompt_path`） | 前后不一致 | 定为 **Segment 级**（视频按 Segment 生成），从 Dialogue 表移到 Segment 表，写明代表 key 规则。 |
| stale 粒度（doc:316 全局单签名 vs §5「重新生成*相关* Plan」doc:310） | 现稿是整板一个签名，任意重排/改字/改时长会 stale 所有 Plan | 明确选粗粒度（删"相关"措辞）还是 per-segment 签名。结合 §5 用语统一。 |

---

## 8. 🟡 次要问题与 nits

- **key lineage 与视觉顺序反序**：在已有 `srt_0004_01` 紧邻右侧时于 `srt_0004` 后插入得 `srt_0004_02`，位置却在 `_01` 之前。doc:249 又说"key 可体现插入 lineage"，与此自相矛盾。建议直接声明 **key 不承载任何顺序/lineage 语义**，杜绝按 key 排序。
- **`segment.dialogue_ids` 字段名误导**：值要求是 `dialogue_asset_key` 却仍叫 `dialogue_ids`（doc:34/306）。整篇文档就是在治这种 id/key 混淆，建议改名 `dialogue_asset_keys`（读取侧兼容），至少加显著 schema 注释。
- **`_NN` 两位序号上限 99**（doc:243），溢出行为未定义，补一句即可。
- **迁移相邻坏 key 顺序未定义**（§8 step 3，doc:385）：左邻自身也是待迁移 manual/重复 key 时，须从左到右、用已迁移后的左邻派生。MIG-01 / NEG-01 未覆盖"两条相邻坏 key"。
- **跨 Scene 派生**：在非首个 Scene 顶部插入会从上一个 Scene 末条派生子 key（全局 key 集 + 左邻跨 Scene）。功能无碍，但应注明"不要从 key 反推 Scene 归属"。

---

## 9. 实现前 must-fix 清单（落地顺序建议）

1. 重写 §7.1：病因改为"新增 Dialogue clone 继承 key / 缺失 key"，定义统一确定性 key 生成函数（修 `KouboStoryBoardModule.jsx:1082` + `kouboAgentChat.js:255`）。
2. `dialogue_id` 稳定化：停止在 `renumberPlan`（FE :63）与 `recalculate`（BE :330）重算 `dialogue_id`；补稳定性断言。
3. **拆改后端运行时 fallback 总闸 `asset_core_services.dialogue_asset_key()`（:129）**：拆成"迁移/加载补齐用生成器"与"运行时只读显式 key 的 accessor"，并修正三类调用点（`storyboard_plan_services.py:335` 写回、`asset_reference_services.py:338+` 取前缀、`video_plan_load_services.py:71` 加载）。这是"禁止运行时 fallback"真正成立的前提（见 §6.1）。
4. 补全 `dialogue_ids` 消费/产出清单：`05_01:196/848`、`06_01:352`、`05_02:1519 flatten_dialogues()` 及其消费者（`05_04:516` 图片回绑、`video_gpt.py:169` prompt 取词）一并改锚 `dialogue_asset_key`。
5. 澄清 Segment/Dialogue 视频路径共用是否有意复用：要么写死"代表 Dialogue 视频 == Segment 视频"约定，要么给 Segment 级产物独立后缀（见 §4，🟠 非 bug）。
6. §6 改为复用 `video_plan_signature_services`，并把 `media_binding_signature` 锚点从 `dialogue_id` 换成 `dialogue_asset_key`；明确扩展 vs 替换、text/duration 是否算 stale。
7. 新增"对象定位 vs 资源绑定"一节（见 §6.5）：保留 `dialogue_id` 编辑入口（`asset_routes.py:1749`、`find_dialogue` :336），仅把文件名/归属切到 `dialogue_asset_key`。
8. 拍板三项开放决策（首条前插入策略 + KEY-00、VideoPrompt 归属、stale 粒度）。
9. 修正 §7.2 两行（`video_plan_load_services` 函数名、`05_05` 单 key），并显式点名 video-only confirm-final 端点（`video_only_plan_routes.py:391`）。

---

## 附：验证覆盖说明

本审核对照真实代码核实了 §7 全部条目、§4 命名后缀、§6 签名机制，新增/重排/Plan 消费链路，以及 Segment final 回绑、video-only confirm-final、asset-bind/clear 对象定位等共十余个关键文件（含 `05_02:2601`、`video_only_plan_routes.py:391`、`asset_routes.py:1749`、`asset_history_services.py:336`）。文档中**所有声称要移除的 manual 符号均不存在**；多 key fallback 清单（§7.2）则大体属实；Segment/Dialogue 视频路径共用经核为**当前一致的有意约定**而非已证 bug（已据此从 🔴 降为 🟠）。结论：需求骨架与测试集保留，§6/§7 须按上文重写后再进入实现。

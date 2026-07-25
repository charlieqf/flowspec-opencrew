# Koubo VideoPlan / Composer 日志排查手册

本文用于排查故事版（口播）里的 VideoPlan 生成、执行、视频合成范围错乱、合成候选缺失、截图里只看到单场景/单片段等问题。

## 快速结论

排查顺序固定为三层：

1. 硬盘产物：确认当前 workspace 里的 `video_generation_plan.json`、执行 state、Composer result 和 Working 视频文件是否一致。
2. 事件日志：用 session debug events 还原是谁在什么时候把当前 VideoPlan 覆盖成了什么范围。
3. UI 截图标记：看弹窗上的 `Task / Session / Req / Plan / Hash / Reason`，判断用户请求范围和当前活跃计划范围是否一致。

## 关键文件

以 `workspace=/Users/macmini-1/.opencrew/sessions/<session_id>/workspace` 为例：

- `SessionOutput/storyboard/koubo_storyboard_edit.json`
  当前 StoryBoard 编辑稿，判断完整业务范围里有多少 shot / scene / dialogue。
- `SessionOutput/storyboard/video_generation_plan.json`
  当前活跃 VideoPlan。Composer 候选主要以它为准。
- `SessionOutput/storyboard/video_generation_plan.ui_cache.json`
  当前 VideoPlan 的 UI 缓存签名、刷新原因、目标范围。
- `SessionOutput/storyboard/video_plan_execution_state.json`
  VideoPlan 执行进度 state。
- `SessionOutput/storyboard/video_plan_execution_result.json`
  VideoPlan 执行结果。
- `SessionOutput/storyboard/video_plan_compose_state.json`
  Composer 后台任务 state。
- `SessionOutput/storyboard/video_plan_compose_result.json`
  Composer 结果。
- `SessionOutput/storyboard/Working/*.mp4`
  片段、场景、镜头、整片实际视频产物。

## UI 截图标记

VideoPlan 弹窗会显示：

- `Task`: OpenCrew task id
- `Session`: OpenCrew session id
- `Req`: 本次 UI 请求的目标范围
- `Plan`: 当前返回 plan 的目标范围
- `Hash`: 当前 plan hash 前 8 位
- `Reason`: cache hit / regenerated / target_mismatch 等原因

Composer 弹窗会显示：

- `Task`
- `Session`
- `Req`: 本次合成候选请求的目标范围
- `Plan`: 当前活跃 VideoPlan 的目标范围
- `Hash`: 当前活跃 VideoPlan hash 前 8 位

典型异常截图：

- `Req=整片`，但 `Plan=shot_001 / scene_002`
  表示用户要合成整片，但当前活跃 VideoPlan 只覆盖单场景。需要重新生成并执行整片 VideoPlan。
- `Plan=整片`，但候选没有 `shot_plan`
  优先查 `video_plan_compose_result.json` 和 Working 里的 `ShotPlan_Final.mp4` 是否存在。
- `Hash` 变化但截图范围没变
  优先查 events 里的 `reason`、`previous_target`、`new_target`。

## 事件日志

使用 debug audience 查询：

```bash
TOKEN=$(PYTHONPATH=backend backend/.venv/bin/python - <<'PY'
from opcrew_backend.config import load_config
from opcrew_backend.context import AppContext
from opcrew_backend.routes.auth import make_token
ctx = AppContext(load_config())
print(make_token(ctx, "admin"))
ctx.shutdown()
PY
)

curl -sS -H "Cookie: opencrew_session=$TOKEN" \
  "http://127.0.0.1:8011/api/sessions/<session_id>/events?audience=debug" \
  | python3 -m json.tool
```

重点事件：

- `koubo_storyboard.video_plan.rerun_started`
  VideoPlan 即将重新生成。关键字段：
  `previous_target`, `previous_plan_hash`, `new_target`, `reason`, `cleanup_actions`, `action_source`
- `koubo_storyboard.video_plan.generated`
  VideoPlan 已生成。关键字段：
  `new_plan_hash`, `shot_count`, `scene_count`, `segment_count`, `summary`, `status`
- `koubo_storyboard.video_plan.execution_started`
  VideoPlan 执行开始。关键字段：
  `source_plan_hash`, `job_id`
- `koubo_storyboard.video_plan.execution_finished`
  VideoPlan 执行结束。关键字段：
  `source_plan_hash`, `status`, `returncode`
- `koubo_storyboard.composer.candidates_checked`
  Composer 候选检查。关键字段：
  `requested_target`, `current_plan_target`, `candidate_count`, `ready_count`, `warnings`, `action_source`
- `koubo_storyboard.composer.scope_mismatch_warning`
  用户请求整片，但当前 plan 是 scene/shot 级。该事件可直接搜索定位范围错乱。
- `koubo_storyboard.composer.started`
  Composer 执行开始。关键字段：
  `target`, `settings`, `job_id`
- `koubo_storyboard.composer.completed`
  Composer 执行完成。关键字段：
  `target`, `summary`, `status`, `returncode`

## 常用只读检查命令

查看当前任务详情和 workspace：

```bash
TOKEN=...
curl -sS -H "Cookie: opencrew_session=$TOKEN" \
  "http://127.0.0.1:8011/api/koubo-storyboard/tasks/<task_id>" \
  | python3 -m json.tool | head -160
```

概览 storyboard / VideoPlan / Composer 文件：

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("/Users/macmini-1/.opencrew/sessions/<session_id>/workspace")
for rel in [
    "SessionOutput/storyboard/koubo_storyboard_edit.json",
    "SessionOutput/storyboard/video_generation_plan.json",
    "SessionOutput/storyboard/video_generation_plan.ui_cache.json",
    "SessionOutput/storyboard/video_plan_execution_state.json",
    "SessionOutput/storyboard/video_plan_compose_state.json",
    "SessionOutput/storyboard/video_plan_compose_result.json",
]:
    p = root / rel
    print("\\n##", rel)
    print("exists", p.exists(), "size", p.stat().st_size if p.exists() else "-")
    if not p.exists():
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    print("target", data.get("target") or data.get("requested_target") or {})
    print("status", data.get("status", ""))
    print("reason", data.get("reason", ""))
    print("plan_hash", data.get("plan_hash", ""))
    shots = data.get("shots") or data.get("plan", {}).get("shots") or []
    scenes = sum(len(s.get("scenes") or []) for s in shots if isinstance(s, dict))
    segments = sum(len(c.get("segments") or []) for s in shots if isinstance(s, dict) for c in (s.get("scenes") or []) if isinstance(c, dict))
    dialogues = sum(len(c.get("dialogues") or []) for s in shots if isinstance(s, dict) for c in (s.get("scenes") or []) if isinstance(c, dict))
    print("shots", len(shots), "scenes", scenes, "segments", segments, "dialogues", dialogues)
PY
```

查看 Working 视频文件：

```bash
find /Users/macmini-1/.opencrew/sessions/<session_id>/workspace/SessionOutput/storyboard/Working \
  -maxdepth 1 -type f -name '*.mp4' -exec ls -lh {} \; | sort
```

检查 Composer 候选：

```bash
TOKEN=...
curl -sS -H "Cookie: opencrew_session=$TOKEN" \
  "http://127.0.0.1:8011/api/koubo-storyboard/tasks/<task_id>/composer/candidates?target_type=task&action_source=manual_debug" \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); print(json.dumps({
    "requested_target": p.get("requested_target"),
    "plan_target": p.get("plan_target"),
    "plan_hash": p.get("plan_hash"),
    "summary": p.get("summary"),
    "warnings": p.get("warnings"),
    "candidates": [
      {
        "id": c.get("id"),
        "kind": c.get("kind"),
        "ready": c.get("ready"),
        "status": (c.get("status") or {}).get("status"),
        "video_path": (c.get("status") or {}).get("video_path"),
        "segment_count": c.get("segment_count"),
        "ready_segment_count": c.get("ready_segment_count"),
      }
      for c in p.get("candidates", [])
    ],
  }, ensure_ascii=False, indent=2))'
```

## 判定规则

- 当前弹窗展示由 `video_generation_plan.json` 和 Composer candidates 决定，不以 Working 里是否还有旧完整视频为准。
- Working 里存在 `ShotPlan_Final.mp4`，但当前 `video_generation_plan.json` 是 scene target 时，前端显示局部候选是符合当前活跃 plan 的。
- `Composer` 的“合成 X/Y 段”指当前 VideoPlan segment 数，不是 StoryBoard 对白条数，也不是素材池卡片数。
- `video_plan_compose_result.json` 可证明历史整片合成是否完成，但如果当前 VideoPlan hash/target 已变化，不能直接代表当前可合成范围。
- 看到 `scope_mismatch_warning` 时，优先让用户重新生成并执行整片 VideoPlan，再打开合成。

## 最小排查流程

1. 从截图读 `Task` 和 `Session`。
2. 读 `Req / Plan / Hash / Reason`。
3. 如果 `Req != Plan`，查 events 中最后几条 `video_plan.rerun_started/generated`。
4. 对比 `previous_target -> new_target` 和 `previous_plan_hash -> new_plan_hash`。
5. 查 `composer.candidates_checked` 的 `candidate_count / ready_count / warnings`。
6. 到硬盘确认当前 `video_generation_plan.json` 与 UI `Plan/Hash` 一致。
7. 再确认 Working 里是否存在历史完整产物，避免误判为文件丢失。

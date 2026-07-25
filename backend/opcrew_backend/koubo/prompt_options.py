from __future__ import annotations

INDUSTRY_OPTIONS = ["医美", "大健康", "口腔", "零售", "教育", "餐饮", "家装"]
PERSONA_OPTIONS = ["强判断老板型", "创始人", "门店总经理", "运营负责人", "专家型主理人"]
TARGET_AUDIENCE_OPTIONS = ["老板", "管理者", "潜在客户", "高净值客户", "加盟商"]
ANALYSIS_GOAL_OPTIONS = ["提取整体公式", "拆解商投结构", "拆解分镜与对白", "评估组件化复刻适配度"]
VIDEO_FORMULA_OPTIONS = ["Hook/Trust/CTA", "老板巡店冲突型", "问题-过程-方案型", "反常识抓钩型"]


def prompt_options_payload() -> dict[str, list[str]]:
    return {
        "industry": INDUSTRY_OPTIONS,
        "persona": PERSONA_OPTIONS,
        "target_audience": TARGET_AUDIENCE_OPTIONS,
        "analysis_goal": ANALYSIS_GOAL_OPTIONS,
        "video_formula": VIDEO_FORMULA_OPTIONS,
    }

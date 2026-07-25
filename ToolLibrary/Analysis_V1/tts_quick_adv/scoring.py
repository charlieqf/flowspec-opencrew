from __future__ import annotations

from typing import Any


SCORING_FULL = "full_speechbrain"
SCORING_DEGRADED = "degraded_resemblyzer_acoustic"
SCORE_SCHEMA_VERSION = "quick_adv_score_v2"


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def normalize_unit(value: float | None) -> float:
    if value is None:
        return 50.0
    return clamp_score(float(value) * 100.0)


def normalize_cosine(value: float | None, low: float = 0.20, high: float = 0.85) -> float:
    if value is None:
        return 50.0
    if high <= low:
        return clamp_score(float(value) * 100.0)
    return clamp_score(((float(value) - low) / (high - low)) * 100.0)


def ratio_score(left: float, right: float) -> float:
    if left <= 0 or right <= 0:
        return 50.0
    return clamp_score((min(left, right) / max(left, right)) * 100.0)


def bounded_inverse_score(delta: float, tolerance: float, hard_limit: float) -> float:
    safe_delta = max(0.0, float(delta))
    safe_tolerance = max(0.0, float(tolerance))
    safe_hard_limit = max(safe_tolerance + 1e-9, float(hard_limit))
    if safe_delta <= safe_tolerance:
        return 100.0
    if safe_delta >= safe_hard_limit:
        return 0.0
    return clamp_score(100.0 * (1.0 - ((safe_delta - safe_tolerance) / (safe_hard_limit - safe_tolerance))))


def quality_penalty_score(*, clipping_risk: float = 0.0, silence_ratio: float = 0.0, duration_error: float = 0.0, rms: float = 0.0) -> float:
    clipping_penalty = clamp_score(float(clipping_risk) * 100.0) * 0.05
    silence_penalty = clamp_score(float(silence_ratio) * 100.0) * 0.03
    duration_penalty = clamp_score(float(duration_error) * 100.0) * 0.02
    low_rms_penalty = 0.0
    if rms <= 0:
        low_rms_penalty = 4.0
    elif rms < 0.015:
        low_rms_penalty = (0.015 - float(rms)) / 0.015 * 4.0
    return clamp_score(clipping_penalty + silence_penalty + duration_penalty + low_rms_penalty)


def absolute_quality_score(*, energy_stability: float = 50.0, duration_fit: float = 100.0, clipping_risk: float = 0.0) -> float:
    return clamp_score(0.45 * clamp_score(energy_stability) + 0.35 * clamp_score(duration_fit) + 0.20 * (100.0 - clamp_score(float(clipping_risk) * 100.0)))


def build_texture_score(*, brightness_score: float, warmth_score: float = 50.0, roughness_score: float = 50.0, nasality_score: float = 50.0) -> float:
    return clamp_score(
        0.35 * brightness_score
        + 0.25 * warmth_score
        + 0.20 * roughness_score
        + 0.20 * nasality_score
    )


def build_articulation_score(*, clarity_score: float, consonant_proxy_score: float = 50.0, sibilance_score: float = 50.0) -> float:
    return clamp_score(
        0.45 * clarity_score
        + 0.30 * consonant_proxy_score
        + 0.25 * sibilance_score
    )


def build_timbre_rank_component(*, resemblyzer_score: float | None, brightness_score: float = 50.0, roughness_score: float = 50.0) -> float:
    spectral_shape_score = clamp_score(0.65 * brightness_score + 0.35 * roughness_score)
    return clamp_score(0.75 * normalize_cosine(resemblyzer_score) + 0.25 * spectral_shape_score)


def build_timbre_score(*, scoring_mode: str, resemblyzer_score: float | None, speechbrain_score: float | None, texture_score: float) -> float:
    if scoring_mode == SCORING_FULL and speechbrain_score is not None:
        return clamp_score(
            0.50 * normalize_cosine(speechbrain_score)
            + 0.30 * normalize_cosine(resemblyzer_score)
            + 0.20 * texture_score
        )
    return clamp_score(
        0.68 * normalize_cosine(resemblyzer_score)
        + 0.32 * texture_score
    )


def build_age_proxy_score(reference_age: str = "", candidate_age: str = "") -> float:
    order = {"child": 0, "young": 1, "adult": 2, "senior": 3}
    left = order.get(str(reference_age or "").strip().lower())
    right = order.get(str(candidate_age or "").strip().lower())
    if left is None or right is None:
        return 50.0
    delta = abs(left - right)
    if delta == 0:
        return 100.0
    if delta == 1:
        return 70.0
    return 30.0


def pitch_band(pitch_hz: float, gender: str = "") -> str:
    pitch = float(pitch_hz or 0.0)
    if pitch <= 0:
        return ""
    normalized_gender = str(gender or "").strip().lower()
    if pitch >= 300:
        return "child_like"
    if normalized_gender == "male":
        if pitch < 125:
            return "low"
        if pitch < 175:
            return "mid"
        return "high"
    if normalized_gender == "female":
        if pitch < 175:
            return "low"
        if pitch < 250:
            return "mid"
        return "high"
    if pitch < 145:
        return "low"
    if pitch < 230:
        return "mid"
    return "high"


def build_pitch_band_score(reference_pitch_hz: float, candidate_pitch_hz: float, *, reference_gender: str = "", candidate_gender: str = "") -> float:
    order = {"low": 0, "mid": 1, "high": 2, "child_like": 3}
    left = order.get(pitch_band(reference_pitch_hz, reference_gender))
    right = order.get(pitch_band(candidate_pitch_hz, candidate_gender))
    if left is None or right is None:
        return 50.0
    delta = abs(left - right)
    if delta == 0:
        return 100.0
    if delta == 1:
        return 70.0
    return 40.0


def build_persona_score(*, gender_score: float, age_proxy_score: float = 50.0, pitch_band_score: float = 50.0) -> float:
    return clamp_score(0.60 * gender_score + 0.25 * age_proxy_score + 0.15 * pitch_band_score)


def build_penalties(*, gender_match: bool = True, pitch_score: float = 100.0, pace_score: float = 100.0, catalog_quality_penalty: float = 0.0) -> dict[str, float]:
    return {
        "gender_mismatch_penalty": 0.0 if gender_match else 12.0,
        "pitch_outlier_penalty": clamp_score((70.0 - float(pitch_score)) / 70.0 * 8.0) if pitch_score < 70 else 0.0,
        "pace_outlier_penalty": clamp_score((70.0 - float(pace_score)) / 70.0 * 8.0) if pace_score < 70 else 0.0,
        "catalog_quality_penalty": clamp_score(catalog_quality_penalty),
    }


def penalty_total(penalties: dict[str, float] | None) -> float:
    if not penalties:
        return 0.0
    return clamp_score(sum(float(value) for value in penalties.values() if isinstance(value, (int, float))))


def build_candidate_explanation(dimension_scores: dict[str, Any]) -> dict[str, Any]:
    label_by_key = {
        "timbre_score": "timbre",
        "pitch_score": "pitch",
        "pace_score": "pace",
        "articulation_score": "articulation",
        "texture_score": "texture",
        "persona_score": "persona",
        "style_score": "style",
    }
    scored = []
    for key, label in label_by_key.items():
        value = dimension_scores.get(key)
        if isinstance(value, (int, float)):
            scored.append((key, label, float(value)))
    ordered = sorted(scored, key=lambda item: item[2], reverse=True)
    watch = [label for _, label, value in sorted(scored, key=lambda item: item[2]) if value < 82.0][:2]
    best = [label for _, label, _ in ordered[:2]]
    summary = "Best dimensions: " + ", ".join(best or ["overall"]) + ("; review: " + ", ".join(watch) if watch else "")
    return {"summary": summary, "best_dimensions": best, "watch_dimensions": watch}


def build_stage1_score(
    *,
    resemblyzer_score: float | None,
    pitch_score: float,
    pace_score: float,
    brightness_score: float,
    gender_score: float,
    catalog_quality_penalty: float = 0.0,
) -> float:
    resemblyzer_normalized = normalize_cosine(resemblyzer_score)
    base = clamp_score(
        0.65 * resemblyzer_normalized
        + 0.13 * pitch_score
        + 0.10 * pace_score
        + 0.07 * brightness_score
        + 0.05 * gender_score
    )
    return clamp_score(base - catalog_quality_penalty)


def build_stage2_score(
    *,
    scoring_mode: str,
    stage1_score: float,
    resemblyzer_score: float | None,
    speechbrain_score: float | None,
    pitch_score: float,
    pace_score: float,
    brightness_score: float,
    energy_score: float,
    clarity_score: float,
    stability_score: float,
    texture_score: float | None = None,
    articulation_score: float | None = None,
    persona_score: float = 50.0,
    style_score: float = 50.0,
    provider_readiness_score: float = 100.0,
    roughness_score: float = 50.0,
    penalties: dict[str, float] | None = None,
) -> float:
    safe_texture_score = texture_score if texture_score is not None else build_texture_score(brightness_score=brightness_score, roughness_score=roughness_score)
    safe_articulation_score = articulation_score if articulation_score is not None else build_articulation_score(clarity_score=clarity_score)
    timbre_rank_component = build_timbre_rank_component(
        resemblyzer_score=resemblyzer_score,
        brightness_score=brightness_score,
        roughness_score=roughness_score,
    )
    stage1_recall_prior = clamp_score(stage1_score)
    if scoring_mode == SCORING_FULL:
        weighted = clamp_score(
            0.42 * normalize_cosine(speechbrain_score)
            + 0.14 * timbre_rank_component
            + 0.10 * stage1_recall_prior
            + 0.08 * pitch_score
            + 0.07 * pace_score
            + 0.07 * safe_texture_score
            + 0.05 * safe_articulation_score
            + 0.03 * persona_score
            + 0.02 * style_score
            + 0.02 * provider_readiness_score
        )
        return clamp_score(weighted - penalty_total(penalties))
    weighted = clamp_score(
        0.24 * timbre_rank_component
        + 0.20 * stage1_recall_prior
        + 0.12 * pitch_score
        + 0.10 * pace_score
        + 0.10 * safe_texture_score
        + 0.08 * safe_articulation_score
        + 0.04 * energy_score
        + 0.04 * persona_score
        + 0.04 * style_score
        + 0.02 * stability_score
        + 0.02 * provider_readiness_score
    )
    return clamp_score(weighted - penalty_total(penalties))


def build_final_score(*, stage2_score: float, prompt_fit_score: float = 100.0, provider_score: float = 100.0, sample_duration_fit_score: float = 100.0, human_review_prior: float = 100.0) -> float:
    return clamp_score(
        0.55 * stage2_score
        + 0.15 * prompt_fit_score
        + 0.10 * provider_score
        + 0.10 * sample_duration_fit_score
        + 0.10 * human_review_prior
    )


def rounded_scores(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = round(float(value), 3) if isinstance(value, (int, float)) else value
    return out

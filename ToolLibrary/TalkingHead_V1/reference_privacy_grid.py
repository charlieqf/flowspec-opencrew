from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from statistics import median
from typing import Any

try:
    from OpenCrew.ToolLibrary.TalkingHead_V1 import privacy_grid_tool as grid_tool
except ImportError:
    from ToolLibrary.TalkingHead_V1 import privacy_grid_tool as grid_tool  # type: ignore


PRIVACY_GRID_MODE = "red_grid_guide"
PRIVACY_GRID_DIR_REL = "SessionContext/TalkingHead_PrivacyGrid"
PRIVACY_GRID_MANIFEST_REL = "SessionOutput/reference/talking_head_privacy_grid_manifest.json"
REFERENCE_SOURCE_STEM_REL = f"{PRIVACY_GRID_DIR_REL}/ReferenceVideo_Source"
REFERENCE_PROVIDER_REL = f"{PRIVACY_GRID_DIR_REL}/ReferenceVideo_PrivacyGrid.mp4"
TARGET_PROVIDER_REL = f"{PRIVACY_GRID_DIR_REL}/TargetIdentity_PrivacyGrid.png"
DEFAULT_REFERENCE_VIDEO = Path(__file__).resolve().parent / "Reference" / "05_02" / "Video_SDR2V_TalkingHead.mp4"
CONTINUITY_PROVIDER_SAFETY_CELL_SIZE_REFERENCE = 12.0
CONTINUITY_PROVIDER_SAFETY_LINE_WIDTH_REFERENCE = 1.0
REFERENCE_VIDEO_PROVIDER_SAFETY_CELL_SIZE_REFERENCE = 12.0
REFERENCE_VIDEO_PROVIDER_SAFETY_LINE_WIDTH_REFERENCE = 1.0


class PrivacyGridError(RuntimeError):
    pass


def text(value: Any) -> str:
    return str(value or "").strip()


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def workspace_path(workspace: Path, value: Any) -> Path:
    path = Path(text(value)).expanduser()
    return path if path.is_absolute() else workspace / path


def rel(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PrivacyGridError(f"隐私网格清单不可读：{path}") from exc
    return payload if isinstance(payload, dict) else {}


def privacy_settings(variables: dict[str, Any]) -> dict[str, Any]:
    talking_head = dict_value(variables.get("talking_head"))
    settings = dict_value(talking_head.get("reference_privacy"))
    return {
        "mode": text(settings.get("reference_privacy_mode")) or PRIVACY_GRID_MODE,
        "apply_to_reference_video": bool(settings.get("apply_privacy_grid_to_reference_video", True)),
        "apply_to_target_identity_image": bool(settings.get("apply_privacy_grid_to_target_identity_image", True)),
        "privacy_grid_preset": text(settings.get("privacy_grid_preset")) or "dense_12_1",
    }


def render_config(variables: dict[str, Any]) -> dict[str, Any]:
    defaults = dict_value(grid_tool.default_variables().get("reference_face_masked_video_build"))
    talking_head = dict_value(variables.get("talking_head"))
    settings = dict_value(talking_head.get("reference_privacy"))
    runtime = dict_value(settings.get("render_config"))
    config = {**defaults, **runtime, "reference_privacy_mode": PRIVACY_GRID_MODE}
    config["privacy_grid"] = {
        **dict_value(defaults.get("privacy_grid")),
        **dict_value(runtime.get("privacy_grid")),
    }
    config["privacy_grid_preset"] = text(runtime.get("privacy_grid_preset") or settings.get("privacy_grid_preset")) or "dense_12_1"
    config["privacy_grid"]["cell_size_reference"] = min(
        max(
            grid_tool.float_value(
                config["privacy_grid"].get("cell_size_reference"),
                grid_tool.PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
            ),
            grid_tool.PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
        ),
        grid_tool.PRIVACY_GRID_MAX_CELL_SIZE_REFERENCE,
    )
    requested_line_width = grid_tool.float_value(config["privacy_grid"].get("line_width_reference"), 1.0)
    config["privacy_grid"]["line_width_reference"] = 0.5 if requested_line_width < 0.75 else 1.0
    return config


def continuity_provider_safety_config(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a provider-safe privacy floor to generated continuity images.

    User-selected sparse presets remain valid for the uploaded identity asset,
    but a generated tail frame is a new photoreal person image.  Sparse grids
    have been rejected by upstream privacy moderation even when grid-presence
    QA passes, so continuity inputs always use the dense 12 x 1 floor.
    """

    grid = dict_value(config.get("privacy_grid"))
    requested_cell = grid_tool.float_value(
        grid.get("cell_size_reference"),
        grid_tool.PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
    )
    requested_line_width = grid_tool.float_value(grid.get("line_width_reference"), 1.0)
    requested_preset = text(grid.get("density_preset") or config.get("privacy_grid_preset"))
    effective_cell = min(requested_cell, CONTINUITY_PROVIDER_SAFETY_CELL_SIZE_REFERENCE)
    effective_line_width = max(requested_line_width, CONTINUITY_PROVIDER_SAFETY_LINE_WIDTH_REFERENCE)
    effective = {
        **config,
        "privacy_grid_preset": "dense_12_1",
        "privacy_grid": {
            **grid,
            "cell_size_reference": effective_cell,
            "line_width_reference": effective_line_width,
            "density_preset": "dense_12_1",
        },
    }
    metadata = {
        "provider_safety_policy": "continuity_dense_12_1_minimum",
        "provider_safety_escalated": bool(
            requested_cell > effective_cell or requested_line_width < effective_line_width
        ),
        "requested_render": {
            "density_preset": requested_preset,
            "cell_size_reference": requested_cell,
            "line_width_reference": requested_line_width,
        },
        "effective_render": {
            "density_preset": "dense_12_1",
            "cell_size_reference": effective_cell,
            "line_width_reference": effective_line_width,
        },
    }
    return effective, metadata


def reference_video_provider_safety_config(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep compressed uploaded reference-video grids visible to moderation."""

    grid = dict_value(config.get("privacy_grid"))
    requested_cell = grid_tool.float_value(
        grid.get("cell_size_reference"),
        grid_tool.PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE,
    )
    requested_line_width = grid_tool.float_value(grid.get("line_width_reference"), 1.0)
    requested_preset = text(grid.get("density_preset") or config.get("privacy_grid_preset"))
    effective_cell = min(requested_cell, REFERENCE_VIDEO_PROVIDER_SAFETY_CELL_SIZE_REFERENCE)
    effective_line_width = max(requested_line_width, REFERENCE_VIDEO_PROVIDER_SAFETY_LINE_WIDTH_REFERENCE)
    effective = {
        **config,
        "privacy_grid_preset": "dense_12_1",
        "privacy_grid": {
            **grid,
            "cell_size_reference": effective_cell,
            "line_width_reference": effective_line_width,
            "density_preset": "dense_12_1",
        },
    }
    metadata = {
        "provider_safety_policy": "uploaded_reference_video_dense_12_1_minimum",
        "provider_safety_escalated": bool(
            requested_cell > effective_cell or requested_line_width < effective_line_width
        ),
        "requested_render": {
            "density_preset": requested_preset,
            "cell_size_reference": requested_cell,
            "line_width_reference": requested_line_width,
        },
        "effective_render": {
            "density_preset": "dense_12_1",
            "cell_size_reference": effective_cell,
            "line_width_reference": effective_line_width,
        },
    }
    return effective, metadata


def require_file(path: Path, label: str) -> None:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        raise PrivacyGridError(f"{label}不存在或为空：{path}")


def copy_source_video(workspace: Path, source: Path) -> Path:
    require_file(source, "参考视频")
    source_suffix = source.suffix.lower() or ".mp4"
    target = workspace / f"{REFERENCE_SOURCE_STEM_REL}{source_suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


def bbox_overlap_of_smaller(left: list[int], right: list[int]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection_width = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_height = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    intersection_area = intersection_width * intersection_height
    smaller_area = min(lw * lh, rw * rh)
    return intersection_area / float(max(1, smaller_area))


def bbox_intersection_area(left: list[int], right: list[int]) -> int:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection_width = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_height = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    return intersection_width * intersection_height


def representative_face_from_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose one detector result without rewarding an oversized duplicate.

    OpenCV Haar cascades commonly report the same face several times.  The old
    implementation chose the largest box, which systematically favored the
    coarse profile cascade and made the final privacy region much too large.
    Confidence selects the preferred detector; distance from the cluster's
    median area breaks ties without inflating the selected region.
    """

    median_area = float(median([
        int(list_value(item.get("bbox"))[2]) * int(list_value(item.get("bbox"))[3])
        for item in cluster
    ]))
    representative = max(
        cluster,
        key=lambda item: (
            float(item.get("confidence") or 0.0),
            -abs(
                int(list_value(item.get("bbox"))[2]) * int(list_value(item.get("bbox"))[3])
                - median_area
            ),
        ),
    )
    return {
        key: value
        for key, value in representative.items()
        if not key.startswith("_")
    } | {
        "cluster_candidate_count": len(cluster),
        "cluster_cascades": sorted({text(item.get("cascade")) for item in cluster if text(item.get("cascade"))}),
    }


def cluster_overlapping_faces(
    candidates: list[dict[str, Any]],
    overlap_threshold: float = 0.55,
) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: int(list_value(item.get("bbox"))[2]) * int(list_value(item.get("bbox"))[3]),
        reverse=True,
    ):
        matching_indexes = [
            index
            for index, cluster in enumerate(clusters)
            if any(
                bbox_overlap_of_smaller(candidate["bbox"], member["bbox"]) >= overlap_threshold
                for member in cluster
            )
        ]
        if not matching_indexes:
            clusters.append([candidate])
            continue
        first_index = matching_indexes[0]
        clusters[first_index].append(candidate)
        for index in reversed(matching_indexes[1:]):
            clusters[first_index].extend(clusters.pop(index))
    return clusters


def expanded_face_bbox(bbox: list[int], width: int, height: int) -> list[int]:
    x, y, face_width, face_height = bbox
    return grid_tool.clamp_bbox(
        [
            int(round(x - 0.15 * face_width)),
            int(round(y - 0.25 * face_height)),
            int(round(face_width * 1.30)),
            int(round(face_height * 1.45)),
        ],
        width,
        height,
    )


def unique_coverage_bboxes(bboxes: list[list[int]], width: int, height: int) -> tuple[list[list[int]], int]:
    """Discard only regions that add no new pixels to the coverage union."""

    unique: list[list[int]] = []
    discarded = 0
    normalized = [grid_tool.clamp_bbox(bbox, width, height) for bbox in bboxes if len(bbox) == 4]
    for candidate in sorted(normalized, key=lambda bbox: bbox[2] * bbox[3], reverse=True):
        candidate_area = candidate[2] * candidate[3]
        if any(bbox_intersection_area(candidate, existing) >= candidate_area for existing in unique):
            discarded += 1
            continue
        unique.append(candidate)
    unique.sort(key=lambda bbox: (bbox[0], bbox[1], bbox[2], bbox[3]))
    return unique, discarded


def privacy_grid_union_masks(
    image_height: int,
    image_width: int,
    bboxes: list[list[int]],
    config: dict[str, Any],
    cv2: Any,
    np: Any,
) -> tuple[Any, Any, dict[str, Any]]:
    """Build one coverage union and one globally aligned grid mask.

    Input rectangles may overlap any number of times.  The boolean coverage
    union and the single output mask guarantee that every output pixel is
    written at most once, so additional faces can add area but never thickness
    or rendering passes inside an already-covered region.
    """

    unique, discarded = unique_coverage_bboxes(bboxes, image_width, image_height)
    if not unique:
        raise PrivacyGridError("隐私网格没有有效覆盖区域。")
    overlap_counts = np.zeros((image_height, image_width), dtype=np.uint16)
    for x, y, width, height in unique:
        overlap_counts[y:y + height, x:x + width] += 1
    coverage = overlap_counts > 0
    line_width, cell, _color = grid_tool.privacy_grid_visual(config, image_width, image_height)
    raster_width = grid_tool.privacy_grid_raster_line_width(line_width)
    line_mask = np.zeros((image_height, image_width), dtype=np.uint8)
    for px in range(0, image_width, cell):
        cv2.line(line_mask, (px, 0), (px, image_height - 1), 255, raster_width)
    for py in range(0, image_height, cell):
        cv2.line(line_mask, (0, py), (image_width - 1, py), 255, raster_width)
    contour_result = cv2.findContours(
        (coverage.astype(np.uint8) * 255),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    contours = contour_result[-2]
    if contours:
        cv2.drawContours(line_mask, contours, -1, 255, raster_width)
    expected_lines = (line_mask > 0) & coverage
    ys, xs = np.where(coverage)
    bounds = [
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    ]
    return coverage, expected_lines, {
        "render_mode": "unique_region_union_once",
        "regions": [
            {
                "bbox": bbox,
                "normalized_region": {
                    "x1": bbox[0] / image_width,
                    "y1": bbox[1] / image_height,
                    "x2": (bbox[0] + bbox[2]) / image_width,
                    "y2": (bbox[1] + bbox[3]) / image_height,
                },
            }
            for bbox in unique
        ],
        "bbox": bounds,
        "normalized_region": {
            "x1": bounds[0] / image_width,
            "y1": bounds[1] / image_height,
            "x2": (bounds[0] + bounds[2]) / image_width,
            "y2": (bounds[1] + bounds[3]) / image_height,
        },
        "raw_region_count": len(bboxes),
        "unique_region_count": len(unique),
        "discarded_contained_region_count": discarded,
        "maximum_input_overlap_depth": int(overlap_counts.max()),
        "maximum_render_count_per_pixel": 1,
        "render_pass_count": 1,
        "region_area_ratio": float(np.mean(coverage)),
        "bounding_box_area_ratio": (bounds[2] * bounds[3]) / float(max(1, image_width * image_height)),
    }


def render_privacy_grid_union(
    frame: Any,
    bboxes: list[list[int]],
    config: dict[str, Any],
    cv2: Any,
    np: Any,
) -> tuple[Any, dict[str, Any]]:
    _coverage, expected_lines, metadata = privacy_grid_union_masks(
        frame.shape[0], frame.shape[1], bboxes, config, cv2, np
    )
    output = frame.copy()
    line_width, _cell, color = grid_tool.privacy_grid_visual(config, frame.shape[1], frame.shape[0])
    opacity = grid_tool.privacy_grid_line_opacity(line_width)
    if opacity >= 1.0:
        output[expected_lines] = color
    else:
        original = output[expected_lines].astype(np.float32)
        target = np.asarray(color, dtype=np.float32)
        output[expected_lines] = np.clip(original * (1.0 - opacity) + target * opacity, 0, 255).astype(np.uint8)
    return output, metadata


def privacy_grid_union_line_presence(
    image: Any,
    bboxes: list[list[int]],
    config: dict[str, Any],
    cv2: Any,
    np: Any,
) -> float:
    _coverage, expected_lines, _metadata = privacy_grid_union_masks(
        image.shape[0], image.shape[1], bboxes, config, cv2, np
    )
    pixels = image[expected_lines]
    if pixels.size == 0:
        return 0.0
    line_width, _cell, _color = grid_tool.privacy_grid_visual(config, image.shape[1], image.shape[0])
    red = grid_tool.privacy_grid_red_pixels(pixels, line_width, np)
    return float(np.mean(red))


def reference_video_stable_regions(
    detections: dict[str, Any],
    image_width: int,
    image_height: int,
    config: dict[str, Any],
) -> tuple[list[list[int]], dict[str, Any]]:
    """Collapse repeated samples of each spatial face into one stable region."""

    candidates: list[dict[str, Any]] = []
    sampled_frames: set[int] = set()
    for segment in list_value(detections.get("segments")):
        for sample in list_value(dict_value(segment).get("samples")):
            frame_index = int(dict_value(sample).get("frame_index") or 0)
            sampled_frames.add(frame_index)
            for face in list_value(dict_value(sample).get("faces")):
                bbox = grid_tool.normalize_detected_bbox(
                    dict_value(face).get("bbox"), image_width, image_height
                )
                if bbox:
                    candidates.append({**dict_value(face), "bbox": bbox, "_frame_index": frame_index})
    if not candidates:
        raise PrivacyGridError("参考视频没有可聚类的人脸区域。")

    tracks = cluster_overlapping_faces(candidates, overlap_threshold=0.30)
    minimum_support = max(2, int(round(max(1, len(sampled_frames)) * 0.20)))
    retained = [
        track
        for track in tracks
        if len({int(item.get("_frame_index") or 0) for item in track}) >= minimum_support
    ]
    if not retained:
        retained = [
            max(
                tracks,
                key=lambda track: len({int(item.get("_frame_index") or 0) for item in track}),
            )
        ]

    _cv2, np = grid_tool.import_cv2_np()
    stable_regions: list[list[int]] = []
    full_motion_regions: list[list[int]] = []
    track_results: list[dict[str, Any]] = []
    retained_face_boxes: list[list[int]] = []
    for track_index, track in enumerate(retained, start=1):
        values = np.asarray([item["bbox"] for item in track], dtype=float)
        widths = values[:, 2]
        heights = values[:, 3]
        left = values[:, 0]
        top = values[:, 1]
        right = values[:, 0] + widths
        bottom = values[:, 1] + heights
        median_width = float(np.median(widths))
        median_height = float(np.median(heights))
        x1 = float(np.percentile(left, 10)) - 0.10 * median_width
        y1 = float(np.percentile(top, 10)) - 0.18 * median_height
        x2 = float(np.percentile(right, 90)) + 0.10 * median_width
        y2 = float(np.percentile(bottom, 90)) + 0.12 * median_height
        bbox = grid_tool.clamp_bbox(
            [
                int(round(x1)),
                int(round(y1)),
                max(1, int(round(x2 - x1))),
                max(1, int(round(y2 - y1))),
            ],
            image_width,
            image_height,
        )
        stable_regions.append(bbox)
        full_motion_regions.append(
            grid_tool.clamp_bbox(
                [
                    int(round(float(np.min(left)) - 0.10 * median_width)),
                    int(round(float(np.min(top)) - 0.18 * median_height)),
                    max(
                        1,
                        int(
                            round(
                                float(np.max(right))
                                + 0.10 * median_width
                                - (float(np.min(left)) - 0.10 * median_width)
                            )
                        ),
                    ),
                    max(
                        1,
                        int(
                            round(
                                float(np.max(bottom))
                                + 0.12 * median_height
                                - (float(np.min(top)) - 0.18 * median_height)
                            )
                        ),
                    ),
                ],
                image_width,
                image_height,
            )
        )
        retained_face_boxes.extend([list(item["bbox"]) for item in track])
        track_results.append({
            "track_id": f"face_track_{track_index:02d}",
            "bbox": bbox,
            "sample_count": len({int(item.get("_frame_index") or 0) for item in track}),
            "detection_count": len(track),
            "median_face_bbox": [
                int(round(float(np.median(values[:, column]))))
                for column in range(4)
            ],
        })

    stable_regions, discarded_contained = unique_coverage_bboxes(
        stable_regions, image_width, image_height
    )
    coverage_ratios: list[float] = []
    for face_bbox in retained_face_boxes:
        face_area = max(1, face_bbox[2] * face_bbox[3])
        coverage_ratios.append(
            max(
                bbox_intersection_area(face_bbox, region) / float(face_area)
                for region in stable_regions
            )
        )
    sample_coverage = sum(value >= 0.95 for value in coverage_ratios) / float(max(1, len(coverage_ratios)))
    area_coverage = float(np.percentile(coverage_ratios, 5)) if coverage_ratios else 0.0
    initial_sample_coverage = sample_coverage
    initial_area_coverage = area_coverage
    motion_bounds_expanded = sample_coverage < 0.95 or area_coverage < 0.90
    if motion_bounds_expanded:
        # Percentile-based stable regions deliberately ignore outlier motion,
        # but a valid uploaded reference may contain a presenter leaning or
        # moving across frame.  Never lower the privacy QA threshold: expand
        # each retained face track to its full observed motion bounds and
        # validate the exact same coverage requirements again.
        stable_regions, fallback_discarded = unique_coverage_bboxes(
            full_motion_regions, image_width, image_height
        )
        discarded_contained += fallback_discarded
        coverage_ratios = []
        for face_bbox in retained_face_boxes:
            face_area = max(1, face_bbox[2] * face_bbox[3])
            coverage_ratios.append(
                max(
                    bbox_intersection_area(face_bbox, region) / float(face_area)
                    for region in stable_regions
                )
            )
        sample_coverage = sum(value >= 0.95 for value in coverage_ratios) / float(max(1, len(coverage_ratios)))
        area_coverage = float(np.percentile(coverage_ratios, 5)) if coverage_ratios else 0.0
    if sample_coverage < 0.95 or area_coverage < 0.90:
        raise PrivacyGridError(
            f"参考视频稳定人脸区域覆盖不足：sample={sample_coverage:.3f}, area={area_coverage:.3f}"
        )
    return stable_regions, {
        "tracking_mode": (
            "spatial_face_track_full_motion_region"
            if motion_bounds_expanded
            else "spatial_face_track_stable_region"
        ),
        "motion_bounds_expanded": motion_bounds_expanded,
        "initial_face_sample_coverage_ratio": round(float(initial_sample_coverage), 6),
        "initial_face_area_coverage_ratio": round(float(initial_area_coverage), 6),
        "track_count": len(stable_regions),
        "tracks": track_results,
        "raw_detection_count": len(candidates),
        "discarded_transient_track_count": len(tracks) - len(retained),
        "discarded_contained_track_count": discarded_contained,
        "minimum_track_sample_support": minimum_support,
        "face_sample_coverage_ratio": round(float(sample_coverage), 6),
        "face_area_coverage_ratio": round(float(area_coverage), 6),
    }


def target_identity_face_regions(
    faces: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Resolve every plausible face after filtering and overlap clustering.

    Haar fallback detectors commonly return the same face from multiple cascades
    and may also report tiny background, clothing, hand, or texture regions.  Tiny
    candidates are filtered, highly overlapping boxes are merged into one face,
    and every remaining spatially distinct face is retained for privacy rendering.
    """

    candidates: list[dict[str, Any]] = []
    frame_area = max(1, image_width * image_height)
    for face in faces:
        candidate = dict_value(face)
        bbox = grid_tool.normalize_detected_bbox(candidate.get("bbox"), image_width, image_height)
        if not bbox:
            continue
        area = int(bbox[2]) * int(bbox[3])
        if area <= 0:
            continue
        center_y_ratio = (int(bbox[1]) + int(bbox[3]) / 2.0) / float(max(1, image_height))
        candidates.append({
            **candidate,
            "bbox": bbox,
            "_area": area,
            "_area_ratio": area / float(frame_area),
            "_center_y_ratio": center_y_ratio,
        })
    if not candidates:
        raise PrivacyGridError("目标人物图未检测到有效人脸区域，不能生成隐私网格。")

    largest_area = max(int(item["_area"]) for item in candidates)
    minimum_area = max(
        int(round(frame_area * 0.002)),
        # A background object in a single-person portrait can occasionally
        # trigger one Haar cascade at just under one fifth of the foreground
        # face area. Keep genuinely distinct people, but reject that weak
        # small-object band before overlap clustering.
        int(round(largest_area * 0.20)),
    )
    plausible = [
        item
        for item in candidates
        if int(item["_area"]) >= minimum_area
        and float(item["_center_y_ratio"]) <= 0.75
    ]
    if not plausible:
        raise PrivacyGridError("目标人物图未检测到可信人脸区域，不能生成隐私网格。")

    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(plausible, key=lambda item: int(item["_area"]), reverse=True):
        matching_indexes = [
            index
            for index, cluster in enumerate(clusters)
            if any(bbox_overlap_of_smaller(candidate["bbox"], member["bbox"]) >= 0.55 for member in cluster)
        ]
        if not matching_indexes:
            clusters.append([candidate])
            continue
        first_index = matching_indexes[0]
        clusters[first_index].append(candidate)
        for index in reversed(matching_indexes[1:]):
            clusters[first_index].extend(clusters.pop(index))

    selected_faces: list[dict[str, Any]] = []
    duplicate_count = 0
    for cluster in clusters:
        representative = max(
            cluster,
            key=lambda item: (
                int(item["_area"]),
                float(item.get("confidence") or 0.0),
            ),
        )
        duplicate_count += max(0, len(cluster) - 1)
        selected_faces.append({
            key: value
            for key, value in representative.items()
            if not key.startswith("_")
        } | {
            "cluster_candidate_count": len(cluster),
            "cluster_cascades": sorted({text(item.get("cascade")) for item in cluster if text(item.get("cascade"))}),
        })

    selected_faces.sort(
        key=lambda item: (
            -(int(list_value(item.get("bbox"))[2]) * int(list_value(item.get("bbox"))[3])),
            int(list_value(item.get("bbox"))[0]),
            int(list_value(item.get("bbox"))[1]),
        )
    )
    return selected_faces, {
        "detected_candidate_count": len(candidates),
        "filtered_candidate_count": len(plausible),
        "deduplicated_candidate_count": duplicate_count,
        "ignored_background_candidate_count": len(candidates) - len(plausible),
    }


def target_identity_primary_face(
    faces: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Compatibility helper for callers that still need one foreground face."""

    selected_faces, summary = target_identity_face_regions(faces, image_width, image_height)
    return selected_faces[0], summary


def reference_video_primary_face(
    faces: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> dict[str, Any] | None:
    """Compatibility helper returning the largest distinct foreground face."""

    selected_faces, _summary = reference_video_face_regions(faces, image_width, image_height)
    return selected_faces[0] if selected_faces else None


def reference_video_face_regions(
    faces: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep every distinct plausible face and collapse detector duplicates."""

    candidates: list[dict[str, Any]] = []
    frame_area = max(1, image_width * image_height)
    for face in faces:
        candidate = dict_value(face)
        bbox = grid_tool.normalize_detected_bbox(candidate.get("bbox"), image_width, image_height)
        if not bbox:
            continue
        x, y, width, height = bbox
        area_ratio = (width * height) / float(frame_area)
        center_y_ratio = (y + height / 2.0) / float(max(1, image_height))
        # Keep smaller secondary faces while rejecting tiny/lower boxes from
        # subtitles, hands, clothing, and floor patterns.
        if area_ratio < 0.002 or center_y_ratio > 0.55:
            continue
        candidates.append({
            **candidate,
            "bbox": bbox,
            "area_ratio": area_ratio,
            "center_y_ratio": center_y_ratio,
        })
    if not candidates:
        return [], {
            "detected_candidate_count": len(faces),
            "filtered_candidate_count": 0,
            "deduplicated_candidate_count": 0,
            "ignored_background_candidate_count": len(faces),
        }
    largest_area = max(int(item["bbox"][2]) * int(item["bbox"][3]) for item in candidates)
    if largest_area / float(frame_area) < 0.01:
        return [], {
            "detected_candidate_count": len(faces),
            "filtered_candidate_count": 0,
            "deduplicated_candidate_count": 0,
            "ignored_background_candidate_count": len(faces),
        }
    plausible = [
        item
        for item in candidates
        if int(item["bbox"][2]) * int(item["bbox"][3]) >= max(int(frame_area * 0.002), int(largest_area * 0.12))
    ]
    clusters = cluster_overlapping_faces(plausible)
    selected = [representative_face_from_cluster(cluster) for cluster in clusters]
    selected.sort(
        key=lambda item: (
            -(int(list_value(item.get("bbox"))[2]) * int(list_value(item.get("bbox"))[3])),
            int(list_value(item.get("bbox"))[0]),
        )
    )
    return selected, {
        "detected_candidate_count": len(faces),
        "filtered_candidate_count": len(plausible),
        "deduplicated_candidate_count": sum(max(0, len(cluster) - 1) for cluster in clusters),
        "ignored_background_candidate_count": len(faces) - len(plausible),
    }


def build_target_identity_grid(
    workspace: Path,
    source: Path,
    config: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    require_file(source, "目标人物图")
    source_rel = rel(workspace, source)
    source_hash = sha256(source)
    if not enabled:
        return {
            "grid_applied": False,
            "skip_reason": "user_disabled",
            "source_path": source_rel,
            "source_sha256": source_hash,
            "provider_path": source_rel,
            "provider_sha256": source_hash,
        }
    cv2, np = grid_tool.import_cv2_np()
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise PrivacyGridError(f"目标人物图无法解码：{source}")
    target_detection_config = {
        **config,
        # Haar minNeighbors=3 produced a large background false positive in a
        # valid single-person portrait.  Keep reference-video detection at its
        # existing default while requiring stronger agreement for still images.
        "opencv_haar_min_neighbors": max(4, grid_tool.int_value(config.get("opencv_haar_min_neighbors"), 4)),
    }
    faces, engine = grid_tool.detect_faces_in_image(image, target_detection_config)
    if not faces:
        raise PrivacyGridError("目标人物图未检测到人脸，不能生成隐私网格。")
    image_height, image_width = image.shape[:2]
    selected_faces, detection_summary = target_identity_face_regions(faces, image_width, image_height)
    face_results: list[dict[str, Any]] = []
    for face in selected_faces:
        bbox = list_value(face.get("bbox"))
        if len(bbox) != 4:
            continue
        x, y, width, height = [int(round(float(value))) for value in bbox]
        expanded = expanded_face_bbox([x, y, width, height], image_width, image_height)
        face_results.append({
            "bbox": [x, y, width, height],
            "expanded_bbox": expanded,
            "confidence": round(float(face.get("confidence") or 0.0), 4),
            "cluster_candidate_count": int(face.get("cluster_candidate_count") or 1),
            "cluster_cascades": list_value(face.get("cluster_cascades")),
        })
    if not face_results:
        raise PrivacyGridError("目标人物图没有有效人脸区域，不能生成隐私网格。")
    coverage_bboxes = [item["expanded_bbox"] for item in face_results]
    rendered, render_metadata = render_privacy_grid_union(image, coverage_bboxes, config, cv2, np)
    min_presence = privacy_grid_union_line_presence(rendered, coverage_bboxes, config, cv2, np)
    if min_presence < 0.95:
        raise PrivacyGridError(f"目标人物图红网格 QA 未通过：{min_presence:.3f}")
    provider = workspace / TARGET_PROVIDER_REL
    provider.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(provider), rendered):
        raise PrivacyGridError(f"目标人物隐私网格图片写入失败：{provider}")
    require_file(provider, "目标人物隐私网格图片")
    return {
        "grid_applied": True,
        "source_path": source_rel,
        "source_sha256": source_hash,
        "provider_path": rel(workspace, provider),
        "provider_sha256": sha256(provider),
        "face_count": len(face_results),
        "faces": face_results,
        # Keep the former single-face fields for consumers that use the first
        # (largest) face as an identity preview.
        "face_bbox": face_results[0]["bbox"],
        "expanded_bbox": face_results[0]["expanded_bbox"],
        "detection_engine": text(engine),
        "coverage_regions": list_value(render_metadata.get("regions")),
        "render_mode": text(render_metadata.get("render_mode")),
        "maximum_input_overlap_depth": int(render_metadata.get("maximum_input_overlap_depth") or 1),
        "maximum_render_count_per_pixel": 1,
        **detection_summary,
        "line_presence_ratio": round(float(min_presence), 4),
        "line_presence_ratio_min": round(float(min_presence), 4),
    }


def reference_video_detections(source: Path, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cv2, _np = grid_tool.import_cv2_np()
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise PrivacyGridError(f"参考视频无法打开：{source}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width <= 0 or height <= 0 or frame_count <= 0:
        cap.release()
        raise PrivacyGridError("参考视频尺寸或帧数无效。")
    stride = max(1, int(round(fps)))
    sample_indexes = sorted(set([0, max(0, frame_count // 2), max(0, frame_count - 1), *range(0, frame_count, stride)]))
    samples: list[dict[str, Any]] = []
    engines: set[str] = set()
    for frame_index in sample_indexes:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            continue
        try:
            faces, engine = grid_tool.detect_faces_in_image(frame, config)
        except Exception:
            continue
        selected_faces, detection_summary = reference_video_face_regions(faces, width, height)
        if selected_faces:
            engines.add(text(engine))
            samples.append({
                "frame_index": frame_index,
                "timestamp_seconds": round(frame_index / fps, 3),
                "faces": selected_faces,
                "raw_candidate_count": len(faces),
                **detection_summary,
            })
    cap.release()
    if not samples:
        raise PrivacyGridError("参考视频未检测到有效人脸，不能生成隐私网格。")
    detections = {
        "schema_version": "talking_head_v1_reference_face_detections_0.1",
        "face_detection_engine": ",".join(sorted(item for item in engines if item)),
        "segments": [{"segment_id": "talking_head_reference", "samples": samples}],
    }
    return detections, {"fps": fps, "width": width, "height": height, "frame_count": frame_count}


def build_reference_video_grid(
    workspace: Path,
    source: Path,
    config: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    source_copy = copy_source_video(workspace, source)
    source_hash = sha256(source_copy)
    if not enabled:
        return {
            "grid_applied": False,
            "skip_reason": "user_disabled",
            "source_path": rel(workspace, source_copy),
            "source_sha256": source_hash,
            "provider_path": rel(workspace, source_copy),
            "provider_sha256": source_hash,
        }
    config, provider_safety = reference_video_provider_safety_config(config)
    detections, probe = reference_video_detections(source_copy, config)
    stable_bboxes, tracking_metadata = reference_video_stable_regions(
        detections, probe["width"], probe["height"], config
    )
    cv2, np = grid_tool.import_cv2_np()
    _coverage, _expected_lines, region = privacy_grid_union_masks(
        probe["height"], probe["width"], stable_bboxes, config, cv2, np
    )
    region.update({
        "region_source": "unique_face_region_union",
        "valid_face_sample_count": int(tracking_metadata.get("raw_detection_count") or 0),
        **tracking_metadata,
    })
    render_bboxes = [list_value(dict_value(item).get("bbox")) for item in list_value(region.get("regions"))]
    cap = cv2.VideoCapture(str(source_copy))
    provider = workspace / REFERENCE_PROVIDER_REL
    provider.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(provider), cv2.VideoWriter_fourcc(*"mp4v"), probe["fps"], (probe["width"], probe["height"]))
    if not writer.isOpened():
        cap.release()
        raise PrivacyGridError(f"参考视频隐私网格输出无法创建：{provider}")
    presences: list[float] = []
    frame_index = 0
    qa_stride = max(1, int(round(probe["fps"])))
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        rendered, _render_metadata = render_privacy_grid_union(frame, render_bboxes, config, cv2, np)
        writer.write(rendered)
        if frame_index % qa_stride == 0:
            presences.append(privacy_grid_union_line_presence(rendered, render_bboxes, config, cv2, np))
        frame_index += 1
    cap.release()
    writer.release()
    require_file(provider, "参考视频隐私网格输出")
    min_presence = min(presences, default=0.0)
    if min_presence < 0.95:
        raise PrivacyGridError(f"参考视频红网格 QA 未通过：{min_presence:.3f}")
    warnings: list[dict[str, Any]] = []
    encode = grid_tool.reencode_reference_video_for_provider(
        provider,
        config,
        width=probe["width"],
        height=probe["height"],
        privacy_mode=PRIVACY_GRID_MODE,
        warnings=warnings,
    )
    return {
        "grid_applied": True,
        "source_path": rel(workspace, source_copy),
        "source_sha256": source_hash,
        "provider_path": rel(workspace, provider),
        "provider_sha256": sha256(provider),
        "fixed_region": region,
        "sample_count": sum(len(list_value(dict_value(item).get("samples"))) for item in list_value(detections.get("segments"))),
        "detection_engine": text(detections.get("face_detection_engine")),
        "line_presence_ratio_min": round(float(min_presence), 4),
        "provider_encode": encode,
        "warnings": warnings,
        **provider_safety,
    }


def materialize_privacy_assets(
    workspace: Path,
    variables: dict[str, Any],
    portrait_path: Any,
    reference_video_path: Any = "",
    *,
    use_system_default: bool = True,
) -> dict[str, Any]:
    settings = privacy_settings(variables)
    if settings["mode"] != PRIVACY_GRID_MODE:
        raise PrivacyGridError(f"Max SD 2 只支持 {PRIVACY_GRID_MODE}，收到：{settings['mode']}")
    portrait = workspace_path(workspace, portrait_path)
    reference = DEFAULT_REFERENCE_VIDEO if use_system_default else workspace_path(workspace, reference_video_path)
    require_file(portrait, "人物形象照片")
    require_file(reference, "参考视频")
    config = render_config(variables)
    target = build_target_identity_grid(workspace, portrait, config, settings["apply_to_target_identity_image"])
    requested_apply_to_reference_video = settings["apply_to_reference_video"]
    apply_to_reference_video = requested_apply_to_reference_video and not use_system_default
    video = build_reference_video_grid(workspace, reference, config, apply_to_reference_video)
    if use_system_default:
        video["skip_reason"] = "system_default_preprocessed"
    scope = (
        "both"
        if apply_to_reference_video and settings["apply_to_target_identity_image"]
        else "reference_video"
        if apply_to_reference_video
        else "target_identity"
        if settings["apply_to_target_identity_image"]
        else "none"
    )
    manifest = {
        "schema_version": "talking_head_v1_privacy_grid_0.1",
        "mode": PRIVACY_GRID_MODE,
        "requested_apply_to_reference_video": requested_apply_to_reference_video,
        "apply_to_reference_video": apply_to_reference_video,
        "apply_to_target_identity_image": settings["apply_to_target_identity_image"],
        "effective_grid_scope": scope,
        "identity_visible": True,
        "privacy_strength": "low",
        "reference_video": video,
        "target_identity": target,
        "render": {
            "line_color": "#ff1f1f",
            "line_width_reference": float(dict_value(config.get("privacy_grid")).get("line_width_reference") or 1),
            "cell_size_reference": int(dict_value(config.get("privacy_grid")).get("cell_size_reference") or grid_tool.PRIVACY_GRID_DEFAULT_CELL_SIZE_REFERENCE),
            "density_preset": settings["privacy_grid_preset"],
            "fill_alpha": 0,
        },
    }
    write_json(workspace / PRIVACY_GRID_MANIFEST_REL, manifest)
    return {**manifest, "manifest_path": PRIVACY_GRID_MANIFEST_REL}


def continuity_requires_grid(segment: dict[str, Any]) -> bool:
    reference = dict_value(segment.get("talking_head_reference"))
    return bool(
        reference.get("privacy_grid_mode")
        and reference.get("target_identity_grid_applied")
    )


def prepare_continuity_frame(
    workspace: Path,
    variables: dict[str, Any],
    segment: dict[str, Any],
    first_frame_path: Path,
    working_dir: Path,
    asset_key: str,
) -> tuple[Path, dict[str, Any]]:
    if not continuity_requires_grid(segment):
        return first_frame_path, {}
    reference = dict_value(segment.get("talking_head_reference"))
    manifest_path = workspace_path(workspace, reference.get("privacy_grid_manifest_path"))
    require_file(manifest_path, "人物口播隐私网格清单")
    manifest = read_json(manifest_path)
    if text(manifest.get("mode")) != PRIVACY_GRID_MODE or not manifest.get("apply_to_target_identity_image"):
        raise PrivacyGridError("尾帧隐私网格配置与清单不一致。")
    require_file(first_frame_path, "连续性尾帧")
    target_manifest = dict_value(manifest.get("target_identity"))
    if text(target_manifest.get("provider_sha256")) and sha256(first_frame_path) == text(target_manifest.get("provider_sha256")):
        return first_frame_path, {
            "grid_applied": True,
            "already_gridded": True,
            "provider_path": rel(workspace, first_frame_path),
            "provider_sha256": sha256(first_frame_path),
        }
    config = render_config(variables)
    config["privacy_grid"] = {**dict_value(config.get("privacy_grid")), **dict_value(manifest.get("render"))}
    config, provider_safety = continuity_provider_safety_config(config)
    cv2, np = grid_tool.import_cv2_np()
    image = cv2.imread(str(first_frame_path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise PrivacyGridError(f"连续性尾帧无法解码：{first_frame_path}")
    target_detection_config = {
        **config,
        "opencv_haar_min_neighbors": max(4, grid_tool.int_value(config.get("opencv_haar_min_neighbors"), 4)),
    }
    faces, engine = grid_tool.detect_faces_in_image(image, target_detection_config)
    if not faces:
        raise PrivacyGridError("连续性尾帧未检测到人脸，禁止发送干净尾帧。")
    height, width = image.shape[:2]
    selected_faces, detection_summary = target_identity_face_regions(faces, width, height)
    face_results: list[dict[str, Any]] = []
    for face in selected_faces:
        bbox = list_value(dict_value(face).get("bbox"))
        if len(bbox) != 4:
            continue
        x, y, face_width, face_height = [int(round(float(value))) for value in bbox]
        expanded = expanded_face_bbox([x, y, face_width, face_height], width, height)
        face_results.append({"bbox": [x, y, face_width, face_height], "expanded_bbox": expanded})
    if not face_results:
        raise PrivacyGridError("连续性尾帧没有有效人脸区域，禁止发送干净尾帧。")
    coverage_bboxes = [item["expanded_bbox"] for item in face_results]
    _coverage, _expected_lines, render_metadata = privacy_grid_union_masks(
        height, width, coverage_bboxes, config, cv2, np
    )
    existing_min_presence = privacy_grid_union_line_presence(image, coverage_bboxes, config, cv2, np)
    if existing_min_presence >= 0.95:
        return first_frame_path, {
            "grid_applied": True,
            "already_gridded": True,
            "provider_path": rel(workspace, first_frame_path),
            "provider_sha256": sha256(first_frame_path),
            "face_count": len(face_results),
            "faces": face_results,
            "detection_engine": text(engine),
            "coverage_regions": list_value(render_metadata.get("regions")),
            "render_mode": text(render_metadata.get("render_mode")),
            "maximum_render_count_per_pixel": 1,
            "line_presence_ratio_min": round(float(existing_min_presence), 4),
            **provider_safety,
            **detection_summary,
        }
    rendered, render_metadata = render_privacy_grid_union(image, coverage_bboxes, config, cv2, np)
    min_presence = privacy_grid_union_line_presence(rendered, coverage_bboxes, config, cv2, np)
    if min_presence < 0.95:
        raise PrivacyGridError(f"连续性尾帧红网格 QA 未通过：{min_presence:.3f}")
    first_frame = dict_value(segment.get("first_frame"))
    materialize = dict_value(first_frame.get("materialize_first_frame"))
    source_type = text(first_frame.get("source_type") or materialize.get("source_type"))
    output_name = (
        f"{asset_key}_ContinuityFirstFrame_PrivacyGrid.png"
        if source_type in {"previous_segment_tail_frame", "previous_scene_tail_frame"}
        else f"{asset_key}_Image_New_PrivacyGrid.png"
    )
    output = working_dir / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), rendered):
        raise PrivacyGridError(f"连续性尾帧隐私网格写入失败：{output}")
    require_file(output, "连续性尾帧隐私网格")
    return output, {
        "grid_applied": True,
        "source_path": rel(workspace, first_frame_path),
        "source_sha256": sha256(first_frame_path),
        "provider_path": rel(workspace, output),
        "provider_sha256": sha256(output),
        "face_count": len(face_results),
        "faces": face_results,
        "detection_engine": text(engine),
        "coverage_regions": list_value(render_metadata.get("regions")),
        "render_mode": text(render_metadata.get("render_mode")),
        "maximum_input_overlap_depth": int(render_metadata.get("maximum_input_overlap_depth") or 1),
        "maximum_render_count_per_pixel": 1,
        "line_presence_ratio_min": round(float(min_presence), 4),
        **provider_safety,
        **detection_summary,
    }


def validate_provider_inputs(workspace: Path, segment: dict[str, Any], identity: Path, reference_video: Path) -> None:
    reference = dict_value(segment.get("talking_head_reference"))
    if not reference.get("privacy_grid_mode"):
        return
    manifest_path = workspace_path(workspace, reference.get("privacy_grid_manifest_path"))
    require_file(manifest_path, "人物口播隐私网格清单")
    manifest = read_json(manifest_path)
    if text(manifest.get("mode")) != PRIVACY_GRID_MODE:
        raise PrivacyGridError("人物口播隐私网格清单模式无效。")
    target = dict_value(manifest.get("target_identity"))
    video = dict_value(manifest.get("reference_video"))
    if not identity.exists() or sha256(identity) != text(target.get("provider_sha256")):
        raise PrivacyGridError("实际目标人物输入与隐私网格清单不一致。")
    if not reference_video.exists() or sha256(reference_video) != text(video.get("provider_sha256")):
        raise PrivacyGridError("实际参考视频输入与隐私网格清单不一致。")
    if bool(target.get("grid_applied")) != bool(reference.get("target_identity_grid_applied")):
        raise PrivacyGridError("目标人物图网格开关与隐私网格清单不一致。")
    if bool(video.get("grid_applied")) != bool(reference.get("reference_video_grid_applied")):
        raise PrivacyGridError("参考视频网格开关与隐私网格清单不一致。")

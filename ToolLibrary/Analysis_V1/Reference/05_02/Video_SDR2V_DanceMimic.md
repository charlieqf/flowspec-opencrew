# 05_02 Video SDR2V DanceMimic Prompt Template

Scope: DanceMimic reference-video-to-video segments routed through `video_openrouter.py` with OpenRouter MaxSR2 / `bytedance/seedance-2.0` and `input_references`.

The image reference assigned the target_identity role anchors subject identity and appearance. A continuity_first_frame reference, when present, preserves cross-segment composition. The reference video is a motion-only guide: use pose timing, body rhythm, camera rhythm, and gesture trajectory, but do not copy the reference person's identity, face, clothing, background, watermark, or mask artifacts.

## English Model-Call Version

<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_START -->
DanceMimic SDR2V task: generate a realistic vertical 9:16 video from the supplied target identity image reference, optional continuity first-frame reference, and motion reference video.

Preserve identity, face, outfit, and body shape from the target identity image reference. Use the continuity first-frame reference only for cross-segment framing, background, and lighting. Use the video reference only for dance motion, pose timing, body rhythm, gesture direction, and camera rhythm.

Target duration: about {{duration_seconds}} seconds.

Generate silent visual motion only. Do not create speech, singing, music, sound effects, voiceover, or any provider-generated audio; OpenCrew will attach the segment audio locally after the video is returned.
<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_START -->
Motion intent and scene note: {{dialogue_text}}
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_START -->
Non-dialogue dance/action shot: keep the first-frame subject and scene, and transfer only motion from the reference video.
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_REFERENCE_ROLES_START -->
Reference-role binding for this request:
{{reference_role_contract}}
Use the target_identity role as the identity anchor and continuity_first_frame only as the continuity anchor. These roles are authoritative even when one image carries both roles.
<!-- OPENCREW:VIDEO_OPENROUTER_REFERENCE_ROLES_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_POSITIVE_START -->
The thin red rectangular grid visible on {{gridded_input_scope}} is a temporary input-only privacy marker. It is not part of the person, face, skin, makeup, clothing, background, or scene. Ignore it completely. The generated video must contain no red grid, red lines, red border, lattice, mesh, tracking marks, or privacy overlay.
<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_POSITIVE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_START -->
identity transfer from reference video, different face, different clothes, different body, copied reference background, face mask grid residue, masked face artifacts, blur over face, occlusion patch, speech, singing, music, sound effects, voiceover, provider-generated audio, subtitles, watermark, logo, text overlays, jump cut, scene reset, low quality, extra limbs, broken hands, distorted face, unsafe motion
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_START -->
mouth-driven talking head, presenter lip sync, product-only commercial cutaway
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_NEGATIVE_START -->
red privacy grid, red rectangular border, red lattice, facial grid, face markings, tracking box, scan lines, privacy marker residue, grid residue
<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_NEGATIVE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_START -->
- The target_identity image reference is the identity anchor. A continuity_first_frame reference is only the continuity anchor.
- The reference video is motion-only. Do not import its face, clothing, background, watermark, mask grid, or mask residue.
- Keep the generated subject's face clear and natural. Do not leave anonymization artifacts.
- Do not treat the reference video as an output video or final video asset.
- Do not generate audio. Return video frames only; OpenCrew replaces/attaches audio locally.
<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_END -->

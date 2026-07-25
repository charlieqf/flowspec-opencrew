from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "router.py"
ANALYSIS_API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "analysisV1Api.js"
TTS_BUILDER_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "AnalysisV1" / "components" / "AnalysisV1TTSBuilder.jsx"


class AnalysisV1ReferenceAudioUploadContractTest(unittest.TestCase):
    def test_backend_streams_uploads_and_extracts_video_audio(self) -> None:
        source = ROUTER_PATH.read_text(encoding="utf-8")
        start = source.index("def analysis_v1_reference_audio_upload_dir")
        end = source.index('@router.get("/api/openclip/tasks/{task_id}/analysis-v1/voice-catalog', start)
        route_source = source[start:end]

        self.assertNotIn("content = await file.read()", route_source)
        self.assertIn("await file.read(1024 * 1024)", route_source)
        self.assertIn('video_suffixes = {".mp4", ".mov", ".m4v"}', route_source)
        self.assertIn("await asyncio.to_thread(subprocess.run", route_source)
        self.assertNotIn("result = subprocess.run([ffmpeg", route_source)
        self.assertIn('"-map", "0:a:0"', route_source)
        self.assertIn("temp_output.replace(output_path)", route_source)
        self.assertIn("source_path.unlink(missing_ok=True)", route_source)
        self.assertIn("temp_output.unlink(missing_ok=True)", route_source)
        self.assertIn('/analysis-v1/tts/reference-audio/chunk', route_source)
        self.assertIn("upload_id: str = Form", route_source)
        self.assertIn("total_chunks: int = Form", route_source)
        self.assertIn("merge_analysis_v1_reference_audio_chunks", route_source)
        self.assertIn("await asyncio.to_thread(merge_analysis_v1_reference_audio_chunks", route_source)
        self.assertIn("shutil.rmtree(chunk_dir, ignore_errors=True)", route_source)
        self.assertIn("finalize_analysis_v1_reference_audio", route_source)
        self.assertIn("bytes_written", route_source)

    def test_frontend_accepts_video_reference_media_with_chinese_errors(self) -> None:
        component_source = TTS_BUILDER_PATH.read_text(encoding="utf-8")
        api_source = ANALYSIS_API_PATH.read_text(encoding="utf-8")

        self.assertIn("video/mp4", component_source)
        self.assertIn(".mp4", component_source)
        self.assertIn(".mov", component_source)
        self.assertIn("视频自动提取音频", component_source)
        self.assertIn("公网隧道/代理", component_source)
        self.assertIn("上传失败：网络连接中断", api_source)
        self.assertNotIn("Upload failed: network error", api_source)
        self.assertIn("TTS_REFERENCE_CHUNK_BYTES", api_source)
        self.assertIn("const TTS_REFERENCE_CHUNK_BYTES = 2 * 1024 * 1024", api_source)
        self.assertIn("const TTS_REFERENCE_CHUNK_THRESHOLD_BYTES = TTS_REFERENCE_CHUNK_BYTES", api_source)
        self.assertIn("uploadTTSReferenceAudioChunked", api_source)
        self.assertIn("file.slice(start, end)", api_source)
        self.assertIn("reference-audio/chunk", api_source)
        self.assertIn("chunk_index", api_source)


if __name__ == "__main__":
    unittest.main()

# Media Binaries

Analysis and Rebuild tools resolve media binaries through `OpenCrew/ToolLibrary/Analysis/media_binaries.py`.

Resolution order:

1. `OPENCREW_FFMPEG_PATH` / `OPENCREW_FFPROBE_PATH` environment variables.
2. Bundled binaries under `OpenCrew/ToolLibrary/.bin/ffmpeg` and `OpenCrew/ToolLibrary/.bin/ffprobe`.
3. System `PATH`.
4. For `ffmpeg` only, `imageio_ffmpeg.get_ffmpeg_exe()` fallback.

Current local fallback observed during Task #22 / Session #53 validation:

```text
/Users/duheng/Library/Python/3.9/lib/python/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1
```

If `ffprobe` is unavailable but `ffmpeg` is available, `01_video_metadata_extractor.py` records `source_backends.ffprobe=false` and continues with OpenCV plus ffmpeg stream parsing.

Recommended persistent setup:

```bash
mkdir -p OpenCrew/ToolLibrary/.bin
ln -sf /path/to/ffmpeg OpenCrew/ToolLibrary/.bin/ffmpeg
ln -sf /path/to/ffprobe OpenCrew/ToolLibrary/.bin/ffprobe
```

Alternative per-run setup:

```bash
export OPENCREW_FFMPEG_PATH=/path/to/ffmpeg
export OPENCREW_FFPROBE_PATH=/path/to/ffprobe
```

Do not patch each tool separately for media binary paths. Prefer updating the environment variables, bundled `.bin` symlinks, or `media_binaries.py`.

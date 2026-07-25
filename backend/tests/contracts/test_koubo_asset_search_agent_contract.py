from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ROUTER_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "router.py"
SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "services.py"
PROVIDERS_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_search_providers.py"
SEARCH_SERVICES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_search_services.py"
SEARCH_ROUTES_PATH = REPO_ROOT / "backend" / "opcrew_backend" / "koubo" / "koubo_storyboard" / "asset_search_routes.py"
KOUBO_API_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "KouboStoryBoard" / "kouboStoryboardApi.js"
OVERLAY_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "UploadAssetLibraryOverlay.jsx"
SIDEBAR_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "components" / "LibrarySidebar.jsx"
WORKSPACE_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "searchAgent" / "SearchAgentWorkspace.jsx"
PANEL_PATH = REPO_ROOT / "frontend" / "src" / "modules" / "koubo" / "UploadAssetLibrary" / "searchAgent" / "SearchAgentPanel.jsx"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class KouboAssetSearchAgentContractTest(unittest.TestCase):
    def test_route_and_service_wiring_is_registered(self) -> None:
        router_source = ROUTER_PATH.read_text(encoding="utf-8")
        services_source = SERVICES_PATH.read_text(encoding="utf-8")
        routes_source = SEARCH_ROUTES_PATH.read_text(encoding="utf-8")

        self.assertIn("from .asset_search_routes import register_asset_search_routes", router_source)
        self.assertIn("register_asset_search_routes(router, deps)", router_source)
        self.assertIn("from .asset_search_services import register_asset_search_services", services_source)
        self.assertIn("register_asset_search_services(ns)", services_source)
        # Phase F removed the module-globals sync machinery: contexts are
        # threaded explicitly, nothing may stuff vars(ns) into module dicts.
        self.assertNotIn("_sync_service_globals", services_source)
        self.assertNotIn("__dict__.update", services_source)
        for route in (
            "/asset-library-search/settings",
            "/asset-library-search/plan",
            "/asset-library-search/storyboard-plan",
            "/asset-library-search/search/events",
            "/asset-library-search/runs",
            "/asset-library-search/import",
            "/asset-library-search/source-list",
        ):
            self.assertIn(route, routes_source)

    def test_provider_normalization_and_pexels_video_endpoint(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_providers as providers

        self.assertEqual(providers.PEXELS_IMAGE_SEARCH_ENDPOINT, "https://api.pexels.com/v1/search")
        self.assertEqual(providers.PEXELS_VIDEO_SEARCH_ENDPOINT, "https://api.pexels.com/v1/videos/search")
        self.assertNotIn("https://api.pexels.com/videos/", PROVIDERS_PATH.read_text(encoding="utf-8"))

        pexels = providers.PexelsProvider("key")
        pixabay = providers.PixabayProvider("key")
        unsplash = providers.UnsplashProvider("key")
        wikimedia = providers.WikimediaProvider()

        pexels_image = pexels.normalize({
            "id": 123,
            "width": 1920,
            "height": 1080,
            "url": "https://www.pexels.com/photo/123/",
            "photographer": "Creator",
            "photographer_url": "https://www.pexels.com/@creator",
            "src": {"original": "https://images.pexels.com/photos/123/photo.jpg", "medium": "https://images.pexels.com/photos/123/medium.jpg"},
        }, "image")
        pixabay_video = pixabay.normalize({
            "id": 456,
            "pageURL": "https://pixabay.com/videos/456/",
            "user": "Pix User",
            "tags": "doctor,hospital",
            "videos": {
                "large": {"url": "https://cdn.pixabay.com/video/456-large.mp4", "width": 1920, "height": 1080},
                "small": {"url": "https://cdn.pixabay.com/video/456-small.mp4", "width": 640, "height": 360},
                "tiny": {"url": "https://cdn.pixabay.com/video/456-tiny.mp4", "width": 320, "height": 180},
            },
            "duration": 8,
        }, "video")
        pexels_video = pexels.normalize({
            "id": 321,
            "width": 3840,
            "height": 2160,
            "url": "https://www.pexels.com/video/321/",
            "image": "https://images.pexels.com/videos/321/poster.jpg",
            "user": {"name": "Video Creator", "url": "https://www.pexels.com/@creator"},
            "video_files": [
                {"link": "https://videos.pexels.com/video-files/321/321-4k.mp4", "width": 3840, "height": 2160, "file_type": "video/mp4"},
                {"link": "https://videos.pexels.com/video-files/321/321-sd.mp4", "width": 960, "height": 540, "file_type": "video/mp4"},
            ],
        }, "video")
        wiki_image = wikimedia.normalize({
            "pageid": 789,
            "title": "File:Hospital corridor.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Hospital.jpg",
                "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Hospital.jpg/640px-Hospital.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Hospital_corridor.jpg",
                "width": 1600,
                "height": 900,
                "mime": "image/jpeg",
                "mediatype": "BITMAP",
                "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": "Author"}},
            }],
        }, "image")
        wiki_unconfirmed = wikimedia.normalize({
            "pageid": 790,
            "title": "File:Unknown.jpg",
            "imageinfo": [{"url": "https://upload.wikimedia.org/x.jpg", "mime": "image/jpeg", "mediatype": "BITMAP", "extmetadata": {}}],
        }, "image")
        wiki_audio = wikimedia.normalize({
            "pageid": 791,
            "title": "File:Bell.ogg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Bell.ogg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bell.ogg",
                "mime": "audio/ogg",
                "mediatype": "AUDIO",
                "extmetadata": {"LicenseShortName": {"value": "Public domain"}, "Artist": {"value": "Author"}},
            }],
        }, "audio")
        unsplash_image = unsplash.normalize({
            "id": "abc123",
            "width": 2400,
            "height": 1600,
            "description": "Hospital corridor",
            "alt_description": "doctor in hospital corridor",
            "urls": {
                "full": "https://images.unsplash.com/photo-abc?ixid=abc&fm=jpg&q=80",
                "regular": "https://images.unsplash.com/photo-abc?ixid=abc&w=1080",
                "small": "https://images.unsplash.com/photo-abc?ixid=abc&w=400",
            },
            "links": {
                "html": "https://unsplash.com/photos/abc123",
                "download_location": "https://api.unsplash.com/photos/abc123/download",
            },
            "user": {"name": "Unsplash Creator", "links": {"html": "https://unsplash.com/@creator"}},
        }, "image")

        self.assertEqual(pexels_image["candidate_id"], "pexels_image_123")
        self.assertEqual(
            pexels_image["allowed_actions"],
            ["preview", "import_whole"],
        )
        self.assertNotIn("open_editor", pexels_image["allowed_actions"])
        self.assertEqual(pixabay_video["media_type"], "video")
        self.assertEqual(pixabay_video["preview_url"], "https://cdn.pixabay.com/video/456-tiny.mp4")
        self.assertEqual(pixabay_video["download_url"], "https://cdn.pixabay.com/video/456-large.mp4")
        self.assertEqual(pexels_video["preview_url"], "https://videos.pexels.com/video-files/321/321-sd.mp4")
        self.assertEqual(pexels_video["download_url"], "https://videos.pexels.com/video-files/321/321-4k.mp4")
        self.assertEqual(wiki_image["license"]["license_status"], "confirmed")
        self.assertEqual(wiki_unconfirmed["license"]["license_status"], "unconfirmed")
        self.assertEqual(wiki_audio["media_type"], "audio")
        self.assertTrue(wiki_audio["import_supported"])
        self.assertEqual(unsplash_image["candidate_id"], "unsplash_image_abc123")
        self.assertTrue(unsplash_image["license"]["requires_attribution"])
        self.assertEqual(unsplash_image["download_tracking_url"], "https://api.unsplash.com/photos/abc123/download")

    def test_pexels_search_uses_v1_video_endpoint(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_providers as providers

        urls: list[str] = []

        async def fake_json_request(url: str, _headers=None, _timeout: int = 30):
            urls.append(url)
            return {"videos": []}

        with patch.object(providers, "json_request", fake_json_request):
            asyncio.run(providers.PexelsProvider("key").search_raw(query="doctor", media_type="video"))

        self.assertTrue(urls)
        self.assertTrue(urls[0].startswith("https://api.pexels.com/v1/videos/search?"))

    def test_unsplash_search_and_import_prepare_track_download(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_providers as providers

        urls: list[str] = []

        async def fake_json_request(url: str, headers=None, _timeout: int = 30):
            urls.append(url)
            if url.startswith("https://api.unsplash.com/search/photos?"):
                self.assertEqual(headers["Authorization"], "Client-ID key")
                return {"results": []}
            if url == "https://api.unsplash.com/photos/abc123/download":
                self.assertEqual(headers["Authorization"], "Client-ID key")
                return {"url": "https://images.unsplash.com/photo-abc?ixid=tracked&fm=jpg"}
            return {}

        provider = providers.UnsplashProvider("key")
        with patch.object(providers, "json_request", fake_json_request):
            asyncio.run(provider.search_raw(query="hospital", media_type="image", aspect="16:9", limit=12))
            prepared = asyncio.run(provider.prepare_import_candidate({
                "provider": "unsplash",
                "provider_asset_id": "abc123",
                "download_tracking_url": "https://api.unsplash.com/photos/abc123/download",
            }))

        self.assertTrue(urls[0].startswith("https://api.unsplash.com/search/photos?"))
        self.assertEqual(urls[1], "https://api.unsplash.com/photos/abc123/download")
        self.assertEqual(prepared["download_url"], "https://images.unsplash.com/photo-abc?ixid=tracked&fm=jpg")

    def test_provider_json_request_sets_user_agent(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_providers as providers

        captured_headers: dict[str, str] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(request, timeout=30):
            del timeout
            captured_headers.update({key.lower(): value for key, value in request.header_items()})
            return FakeResponse()

        with patch.object(providers.urllib.request, "urlopen", fake_urlopen):
            providers._json_request_sync("https://commons.wikimedia.org/w/api.php", {"Accept": "application/json"})

        self.assertEqual(captured_headers.get("user-agent"), providers.ASSET_SEARCH_USER_AGENT)

    def test_ssrf_host_allowlist_rejects_lookalikes_and_final_redirect_is_checked(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_providers as providers

        allowed = providers.validate_provider_url("pexels", "https://PEXELS.com/video/123")
        self.assertEqual(allowed, "https://PEXELS.com/video/123")
        for url in (
            "https://evilpexels.com/video.mp4",
            "https://pexels.com.evil.com/video.mp4",
            "https://127.0.0.1/video.mp4",
            "https://xn--pexels-9ib.com/video.mp4",
        ):
            with self.subTest(url=url):
                with self.assertRaises(providers.AssetSearchSecurityError):
                    providers.validate_provider_url("pexels", url)
        provider_source = PROVIDERS_PATH.read_text(encoding="utf-8")
        self.assertIn("urllib.parse.urljoin(current_url, location)", provider_source)
        self.assertIn("validate_provider_url(provider_id, response.geturl() or current_url)", provider_source)

    def test_pixabay_cache_keeps_24h_response_without_repeat_request(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeProvider:
            provider_id = "pixabay"
            calls = 0

            async def search_raw(self, **_kwargs):
                self.calls += 1
                return {"hits": [{"id": self.calls}]}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sn = SimpleNamespace(read_json=read_json, write_json=write_json)
            with patch.object(services, "_sc", sn, create=True):
                provider = FakeProvider()
                first, first_cached = asyncio.run(services._cached_provider_response(workspace, provider, query="doctor", media_type="image", aspect="16:9", limit=12, page=1, language="en", safe_search=True, sc=sn))
                second, second_cached = asyncio.run(services._cached_provider_response(workspace, provider, query="doctor", media_type="image", aspect="16:9", limit=12, page=1, language="en", safe_search=True, sc=sn))

            cache_files = list((workspace / "SessionContext/AssetSearchAgent/Cache/pixabay").glob("*.json"))
            cache_payload = read_json(cache_files[0])

        self.assertEqual(first, second)
        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(cache_payload["expires_at"] - cache_payload["cached_at"], services.ASSET_SEARCH_PIXABAY_TTL_MS)

    def test_provider_error_detail_redacts_pixabay_query_key(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.text_utils import redact_secret_text

        class FailingPixabayProvider:
            provider_id = "pixabay"

            async def search_raw(self, **_kwargs):
                raise RuntimeError("failed url https://pixabay.com/api/?key=secret-pixabay-key&q=doctor")

        with tempfile.TemporaryDirectory() as tmp:
            sn = SimpleNamespace(read_json=lambda _path: {}, write_json=lambda *_args, **_kwargs: None, redact_secret_text=redact_secret_text)
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "provider_for", lambda *_args, **_kwargs: FailingPixabayProvider()),
                patch.object(services, "_provider_credentials", lambda **_sc_kwargs: {"pixabay": "secret-pixabay-key"}),
            ):
                _items, stats = asyncio.run(services._search_provider_candidates(
                    Path(tmp),
                    "pixabay",
                    {"queries": [{"query": "doctor", "media_type": "image", "language": "en"}]},
                    {"media_types": ["image"], "aspect": "auto", "limit_per_source": 12, "language": "en", "safe_search": True},
                    services.default_asset_search_settings(),
                sc=sn))

        self.assertEqual(stats["status"], "error")
        self.assertNotIn("secret-pixabay-key", stats["detail"])
        self.assertIn("https://pixabay.com/api/?***", stats["detail"])

    def test_chinese_spring_image_query_keeps_intent_and_variants(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        translated, translated_ok = services.translate_search_text_to_english_keywords("春暖花开的图片")

        self.assertTrue(translated_ok)
        self.assertIn("spring", translated)
        self.assertIn("flowers", translated)
        self.assertIn("blossoms", translated)
        self.assertNotEqual(translated, "photo image")

        request = {
            "user_text": "春暖花开的图片",
            "media_types": ["image"],
            "aspect": "auto",
            "sources": ["pixabay"],
            "language": "en",
        }
        fallback = services.fallback_asset_search_plan(request)
        self.assertIn("spring", fallback["queries"][0]["query"])
        self.assertIn("春暖花开的图片", fallback["queries"][0]["query_variants"])

        normalized = services.normalize_planner_result(
            {
                "request_id": "req_1",
                "media_types": ["image"],
                "aspect": "auto",
                "sources": ["pixabay"],
                "queries": [{"query": "bright natural stock photo", "media_type": "image", "language": "en"}],
            },
            request,
            "req_1",
        )
        query = normalized["queries"][0]
        self.assertIn("spring", query["query"])
        self.assertIn("flowers", query["query"])
        self.assertIn("春暖花开的图片", query["query_variants"])

    def test_wikimedia_search_uses_provider_specific_query_fallbacks(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        variants = services.asset_search_provider_query_variants(
            "wikimedia",
            "doctor tablet hospital corridor realistic documentary b-roll",
            "image",
        )
        self.assertEqual(variants[0], "doctor tablet hospital corridor realistic documentary b-roll")
        self.assertIn("doctor tablet hospital corridor", variants)
        self.assertIn("hospital corridor", variants)
        self.assertEqual(services.asset_search_provider_query_variants("pexels", "doctor tablet hospital corridor", "image"), ["doctor tablet hospital corridor"])

        class FakeWikimediaProvider:
            provider_id = "wikimedia"

            async def search_raw(self, **kwargs):
                return {"query": kwargs["query"], "pages": [{"pageid": 1}]} if kwargs["query"] == "hospital corridor" else {"query": kwargs["query"], "pages": []}

            def response_items(self, response, _media_type):
                return response["pages"]

            def normalize(self, item, media_type, _index):
                return {
                    "candidate_id": f"wikimedia_{media_type}_{item['pageid']}",
                    "provider": "wikimedia",
                    "provider_asset_id": str(item["pageid"]),
                    "media_type": media_type,
                    "title": "Hospital corridor",
                    "download_url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/Hospital.jpg",
                    "source_url": "https://commons.wikimedia.org/wiki/File:Hospital.jpg",
                    "score": 0.7,
                    "import_supported": True,
                    "score_reasons": [],
                }

        with tempfile.TemporaryDirectory() as tmp:
            sn = SimpleNamespace(read_json=lambda _path: {}, write_json=lambda *_args, **_kwargs: None)
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "provider_for", lambda *_args, **_kwargs: FakeWikimediaProvider()),
            ):
                candidates, stats = asyncio.run(services._search_provider_candidates(
                    Path(tmp),
                    "wikimedia",
                    {"queries": [{"query": "doctor tablet hospital corridor realistic documentary b-roll", "media_type": "image", "language": "en"}]},
                    {"media_types": ["image"], "aspect": "auto", "limit_per_source": 12, "language": "en", "safe_search": True},
                    services.default_asset_search_settings(),
                sc=sn))

        self.assertEqual(stats["status"], "ok")
        self.assertEqual(stats["kept"], 1)
        self.assertGreater(stats["requested"], 1)
        self.assertIn("hospital corridor", stats["fallback_queries"])
        self.assertEqual(candidates[0]["query"], "hospital corridor")
        self.assertEqual(candidates[0]["original_query"], "doctor tablet hospital corridor realistic documentary b-roll")

    def test_provider_search_tries_chinese_query_variant_before_english_hint(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        calls: list[dict[str, object]] = []

        class FakePixabayProvider:
            provider_id = "pixabay"

            async def search_raw(self, **kwargs):
                calls.append(kwargs)
                return {
                    "hits": [{"id": 1, "query": kwargs["query"]}] if kwargs["query"] == "spring blossoms flowers warm spring photo image" else []
                }

            def response_items(self, response, _media_type):
                return response["hits"]

            def normalize(self, item, media_type, _index):
                return {
                    "candidate_id": f"pixabay_{media_type}_{item['id']}",
                    "provider": "pixabay",
                    "provider_asset_id": str(item["id"]),
                    "media_type": media_type,
                    "title": "Spring blossoms",
                    "preview_url": "https://cdn.pixabay.com/photo/spring.jpg",
                    "download_url": "https://cdn.pixabay.com/photo/spring.jpg",
                    "source_url": "https://pixabay.com/photos/spring-1/",
                    "score": 0.7,
                    "import_supported": True,
                    "score_reasons": [],
                }

        request = {
            "media_types": ["image"],
            "aspect": "auto",
            "limit_per_source": 12,
            "language": "en",
            "safe_search": True,
        }
        plan = {
            "queries": [{
                "query": "spring blossoms flowers warm spring photo image",
                "query_variants": ["春暖花开的图片", "spring blossoms flowers warm spring photo image"],
                "media_type": "image",
                "language": "en",
            }],
        }

        with tempfile.TemporaryDirectory() as tmp:
            sn = SimpleNamespace(read_json=lambda _path: {}, write_json=lambda *_args, **_kwargs: None)
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "provider_for", lambda *_args, **_kwargs: FakePixabayProvider()),
                patch.object(services, "_provider_credentials", lambda **_sc_kwargs: {"pixabay": "key"}),
            ):
                candidates, stats = asyncio.run(services._search_provider_candidates(
                    Path(tmp),
                    "pixabay",
                    plan,
                    request,
                    services.default_asset_search_settings(),
                sc=sn))

        self.assertEqual([item["query"] for item in calls[:2]], ["春暖花开的图片", "spring blossoms flowers warm spring photo image"])
        self.assertEqual(calls[0]["language"], "zh")
        self.assertEqual(calls[1]["language"], "en")
        self.assertEqual(stats["status"], "ok")
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(candidates[0]["query"], "spring blossoms flowers warm spring photo image")

    def test_stream_search_runs_providers_concurrently(self) -> None:
        source = SEARCH_SERVICES_PATH.read_text(encoding="utf-8")

        self.assertIn("asyncio.create_task(_run_asset_search_provider", source)
        self.assertIn("asyncio.as_completed(provider_tasks)", source)

    def test_asset_search_credentials_store_in_secret_store_without_returning_keys(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeSecretStore:
            def __init__(self) -> None:
                self.values: dict[str, str] = {}

            def set(self, key: str, value: str) -> None:
                self.values[key] = value

            def get(self, key: str) -> str:
                return self.values.get(key, "")

            def has(self, key: str) -> bool:
                return key in self.values

        class FakeConn:
            def execute(self, *_args, **_kwargs):
                return None

        class FakeEngine:
            def begin(self):
                class Ctx:
                    def __enter__(self_nonlocal):
                        return FakeConn()

                    def __exit__(self_nonlocal, *_args):
                        return False
                return Ctx()

        class FakeCtx:
            def __init__(self) -> None:
                self.secret_store = FakeSecretStore()
                self.engine = FakeEngine()
                self.events: list[dict[str, object]] = []

            def event(self, level, category, message, payload=None) -> None:
                self.events.append({"level": level, "category": category, "message": message, "payload": payload or {}})

        fake_ctx = FakeCtx()
        sn = SimpleNamespace(ctx=fake_ctx)
        with (
            patch.object(services, "_sc", sn, create=True),
            patch.object(services, "ensure_media_config_table", lambda _ctx: None),
        ):
            services.save_asset_search_provider_keys({"provider_keys": {"unsplash": "unsplash-secret"}}, sc=sn)
            status = services.asset_search_provider_status(services.default_asset_search_settings(), sc=sn)

        self.assertEqual(fake_ctx.secret_store.values["asset_search_unsplash_key"], "unsplash-secret")
        self.assertTrue(status["unsplash"]["configured"])
        self.assertNotIn("unsplash-secret", json.dumps(status))

    def test_stream_search_uses_user_edited_plan(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        captured: dict[str, object] = {}

        async def fake_search_provider_candidates(_workspace, provider_id, plan, request, _settings, **_sc_kwargs):
            captured["provider"] = provider_id
            captured["plan"] = plan
            captured["request"] = request
            return ([{
                "candidate_id": "wikimedia_audio_1",
                "provider": "wikimedia",
                "provider_asset_id": "1",
                "media_type": "audio",
                "title": "Bell",
                "score": 0.8,
                "import_supported": True,
            }], {"requested": 1, "returned": 1, "kept": 1, "status": "ok"})

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            events: list[dict[str, object]] = []
            sn = SimpleNamespace(workspace_for=lambda _task: workspace, write_json=write_json, add_event=lambda *_args, **_kwargs: None)
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "read_asset_search_settings", lambda _task, **_sc_kwargs: services.default_asset_search_settings()),
                patch.object(services, "_search_provider_candidates", fake_search_provider_candidates),
            ):
                async def collect():
                    async for event in services.stream_asset_search_events(task, {
                        "text": "bell sound",
                        "media_types": ["audio"],
                        "sources": ["wikimedia"],
                        "plan": {
                            "media_types": ["audio"],
                            "sources": ["wikimedia"],
                            "queries": [{"query": "church bell sound", "media_type": "audio", "language": "en", "priority": 1}],
                        },
                    }, sc=sn):
                        events.append(event)

                asyncio.run(collect())

        self.assertEqual(captured["plan"]["queries"][0]["query"], "church bell sound")
        self.assertEqual(captured["plan"]["queries"][0]["media_type"], "audio")
        self.assertEqual(events[-1]["type"], "completed")

    def test_storyboard_batch_plan_and_text_embedding_rerank(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        storyboard = {
            "shots": [{
                "shot_id": "shot_001",
                "title": "Hospital corridor",
                "scenes": [{
                    "scene_id": "scene_001",
                    "description": "doctor checks tablet in a bright hospital corridor",
                    "dialogues": [{"text": "The patient status is stable."}],
                }],
            }],
        }
        task = {"id": 5, "session_id": 6}
        sn = SimpleNamespace(load_plan=lambda _task, **_sc_kwargs: (storyboard, {}), add_event=lambda *_args, **_kwargs: None)
        with (
            patch.object(services, "_sc", sn, create=True),
            patch.object(services, "read_asset_search_settings", lambda _task, **_sc_kwargs: services.default_asset_search_settings()),
        ):
            plan = asyncio.run(services.create_storyboard_asset_search_plan(task, {"media_types": ["image"], "sources": ["wikimedia"]}, sc=sn))

        self.assertEqual(plan["batch_scope"], "storyboard")
        self.assertTrue(plan["edited"])
        self.assertEqual(plan["queries"][0]["storyboard_ref"]["shot_id"], "shot_001")
        self.assertEqual(plan["queries"][0]["storyboard_ref"]["scene_id"], "scene_001")

        candidates, meta = services.rerank_asset_search_candidates([
            {"candidate_id": "weak", "title": "cartoon beach", "description": "", "query": "", "score": 0.5, "score_reasons": []},
            {"candidate_id": "strong", "title": "doctor in hospital corridor", "description": "tablet", "query": "", "score": 0.5, "score_reasons": []},
        ], plan, services.default_asset_search_settings())

        self.assertTrue(meta["enabled"])
        self.assertEqual(candidates[0]["candidate_id"], "strong")
        self.assertIn("embedding_score", candidates[0])

    def test_asset_search_source_list_export_writes_json_and_markdown(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_assets.json", {"assets": [{
                "path": "SessionOutput/storyboard/assets/images/example.jpg",
                "label": "Example",
                "kind": "image",
                "origin": {
                    "tool": "asset_search_agent",
                    "search_id": "search_1",
                    "candidate_id": "wikimedia_image_1",
                    "provider": "wikimedia",
                    "media_type": "image",
                    "provider_asset_id": "1",
                    "source_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
                    "creator": {"name": "Creator"},
                    "license": {"name": "CC BY-SA 4.0", "license_status": "confirmed", "attribution_text": "Creator"},
                    "storyboard_ref": {"shot_id": "shot_001", "scene_id": "scene_001"},
                },
            }]})
            sn = SimpleNamespace(workspace_for=lambda _task: workspace, read_json=read_json, write_json=write_json, add_event=lambda *_args, **_kwargs: None)
            with patch.object(services, "_sc", sn, create=True):
                result = services.export_asset_search_source_list(task, sc=sn)

            exported_json = read_json(workspace / result["json_path"])
            exported_md = (workspace / result["markdown_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["item_count"], 1)
        self.assertEqual(exported_json["items"][0]["candidate_id"], "wikimedia_image_1")
        self.assertIn("CC BY-SA 4.0", exported_md)
        self.assertIn("source", exported_md)

    def test_local_asset_library_search_returns_reusable_candidates(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.io_utils import safe_workspace_rel

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            asset_path = workspace / "SessionOutput/storyboard/assets/images/doctor_corridor.jpg"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(b"\xff\xd8\xff\xe0jpeg")
            manifest_item = {
                "id": "SessionOutput/storyboard/assets/images/doctor_corridor.jpg",
                "path": "SessionOutput/storyboard/assets/images/doctor_corridor.jpg",
                "filename": "doctor_corridor.jpg",
                "label": "Doctor checks tablet in hospital corridor",
                "kind": "image",
                "source": "upload",
                "origin": {"tool": "upload", "prompt": "doctor tablet hospital corridor"},
            }
            write_json(workspace / "SessionOutput/storyboard/koubo_storyboard_assets.json", {"assets": [manifest_item]})
            plan = {
                "summary": "doctor in hospital corridor",
                "queries": [{"query": "doctor hospital corridor", "media_type": "image", "language": "en"}],
                "must_have": ["doctor", "hospital"],
                "nice_to_have": ["tablet", "corridor"],
            }
            request = {"user_text": "doctor hospital corridor", "media_types": ["image"], "aspect": "auto", "limit_per_source": 12, "language": "en", "safe_search": True}

            sn = SimpleNamespace(read_json=read_json, write_json=write_json, workspace_for=lambda _task: workspace, safe_workspace_rel=safe_workspace_rel, add_event=lambda *_args, **_kwargs: None, load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {}))
            with patch.object(services, "_sc", sn, create=True):
                candidates, stats = services._search_local_asset_candidates(workspace, task, plan, request, services.default_asset_search_settings(), sc=sn)
                write_json(workspace / "SessionContext/AssetSearchAgent/SearchRuns/search_4000_abcd.json", {
                    "search_id": "search_4000_abcd",
                    "task_id": 5,
                    "session_id": 6,
                    "created_at": services.now_ms(),
                    "candidates": candidates,
                })
                result = asyncio.run(services.import_asset_search_candidates(task, {
                    "search_id": "search_4000_abcd",
                    "candidate_ids": [candidates[0]["candidate_id"]],
                }, sc=sn))

        self.assertEqual(stats["status"], "ok")
        self.assertEqual(stats["kept"], 1)
        self.assertEqual(candidates[0]["provider"], "local")
        self.assertTrue(candidates[0]["local_reuse"])
        self.assertEqual(
            candidates[0]["allowed_actions"],
            ["preview", "reuse_local"],
        )
        self.assertTrue(candidates[0]["preview_url"].startswith("/api/session-tasks/6/raw/"))
        self.assertTrue(result["imported"][0]["skipped"])
        self.assertTrue(result["imported"][0]["local_reuse"])
        self.assertEqual(result["failed"], [])

    def test_media_library_source_settings_and_candidate_actions_are_distinct(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        settings = services.default_asset_search_settings()
        sn = SimpleNamespace(ctx=SimpleNamespace())
        status = services.asset_search_provider_status(settings, sc=sn)

        self.assertIn("media_library", services.ASSET_SEARCH_PROVIDERS)
        self.assertTrue(settings["sources"]["media_library"]["enabled"])
        self.assertTrue(status["media_library"]["configured"])
        self.assertEqual(
            services._allowed_actions_for_source("local"),
            ["preview", "reuse_local"],
        )
        self.assertEqual(
            services._allowed_actions_for_source("media_library"),
            ["preview", "open_editor", "import_original"],
        )
        self.assertEqual(
            services._allowed_actions_for_source(
                "media_library", "derived_clip"
            ),
            ["preview", "import_clip"],
        )
        for provider in ("pexels", "pixabay", "wikimedia", "unsplash"):
            self.assertEqual(
                services._allowed_actions_for_source(provider),
                ["preview", "import_whole"],
            )
            self.assertNotIn(
                "open_editor",
                services._allowed_actions_for_source(provider),
            )

    def test_media_library_adapter_reuses_shared_service_and_preserves_agent_sse(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeSearchService:
            def __init__(self) -> None:
                self.requests: list[dict[str, object]] = []

            async def search(self, request):
                self.requests.append(dict(request))
                return SimpleNamespace(
                    search_id="mls_1700000000000_shared",
                    total_count=1,
                    items=[
                        {
                            "source": "media_library",
                            "candidate_id": "mla_global_1",
                            "asset_id": "mla_global_1",
                            "source_version": "a" * 64,
                            "display_name": "全局采访原片",
                            "preview_url": "/api/media-library/mla_global_1/video",
                            "thumbnail_url": "/api/media-library/mla_global_1/thumbnail",
                            "duration_ms": 12500,
                            "orientation": "portrait",
                            "score": 0.91,
                            "raw_score": 182.0,
                            "score_reasons": ["对白原句命中"],
                            "matched_fragments": [
                                {
                                    "scheme": "dialogue",
                                    "run_id": "mlar_dialogue_1",
                                    "fragment_id": "srt_0001",
                                    "start_ms": 1000,
                                    "end_ms": 2800,
                                    "dialogue_text": "产品具有防水能力",
                                    "raw_score": 100.0,
                                    "score_reasons": ["完整原始查询命中对白"],
                                },
                                {
                                    "scheme": "dialogue",
                                    "run_id": "mlar_dialogue_1",
                                    "fragment_id": "srt_0002",
                                    "start_ms": 3000,
                                    "end_ms": 4300,
                                    "dialogue_text": "下雨也可以放心使用",
                                    "raw_score": 82.0,
                                    "score_reasons": ["规划关键词命中"],
                                },
                            ],
                            "allowed_actions": [
                                "preview",
                                "open_editor",
                                "import_original",
                            ],
                        }
                    ],
                )

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 27, "session_id": 31, "workspace_dir": str(workspace)}
            shared = FakeSearchService()
            settings = services.default_asset_search_settings()
            for source in settings["sources"].values():
                source["enabled"] = False
            settings["sources"]["media_library"]["enabled"] = True
            events: list[dict[str, object]] = []
            sn = SimpleNamespace(
                ctx=SimpleNamespace(media_library_search_service=shared),
                workspace_for=lambda _task: workspace,
                write_json=write_json,
                add_event=lambda *_args, **_kwargs: None,
                redact_secret_text=lambda value: str(value),
            )
            with (
                patch.object(
                    services,
                    "read_asset_search_settings",
                    lambda _task, **_kwargs: settings,
                ),
                patch.object(
                    services,
                    "provider_for",
                    side_effect=AssertionError(
                        "media_library must not use provider_for"
                    ),
                ),
            ):
                async def collect() -> None:
                    async for event in services.stream_asset_search_events(
                        task,
                        {
                            "text": "防水能力",
                            "media_types": ["video"],
                            "sources": ["media_library"],
                            "plan": {
                                "summary": "waterproof product",
                                "media_types": ["video"],
                                "sources": ["media_library"],
                                "queries": [
                                    {
                                        "query": "waterproof product",
                                        "media_type": "video",
                                        "language": "en",
                                    }
                                ],
                            },
                        },
                        sc=sn,
                    ):
                        events.append(event)

                asyncio.run(collect())

            run_files = list(
                (
                    workspace
                    / "SessionContext/AssetSearchAgent/SearchRuns"
                ).glob("search_*.json")
            )
            run = read_json(run_files[0])

        self.assertEqual(len(shared.requests), 1)
        self.assertEqual(shared.requests[0]["query"], "防水能力")
        self.assertEqual(shared.requests[0]["entry_point"], "agent")
        self.assertEqual(shared.requests[0]["target_task_id"], 27)
        self.assertEqual(shared.requests[0]["sources"], ["media_library"])
        self.assertEqual(events[0]["type"], "started")
        self.assertIn(
            "candidate.batch", [event["type"] for event in events]
        )
        self.assertEqual(events[-1]["type"], "completed")
        self.assertEqual(run["provider_stats"]["media_library"]["status"], "ok")
        self.assertEqual(len(run["candidates"]), 1)
        candidate = run["candidates"][0]
        self.assertEqual(candidate["provider"], "media_library")
        self.assertEqual(candidate["provider_asset_id"], "mla_global_1")
        self.assertEqual(candidate["asset_id"], "mla_global_1")
        self.assertEqual(candidate["candidate_id"], "mla_global_1")
        self.assertEqual(candidate["source_version"], "a" * 64)
        self.assertTrue(candidate["global_media_library"])
        self.assertFalse(candidate["local_reuse"])
        self.assertEqual(len(candidate["matched_fragments"]), 2)
        self.assertEqual(candidate["download_url"], "")
        self.assertEqual(
            candidate["allowed_actions"],
            ["preview", "open_editor", "import_original"],
        )

    def test_media_library_agent_preserves_derived_clip_identity_and_dispatches_import_clip(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        clip_id = "mlc_1700000000000_aaaaaaaaaaaa"
        adapted = services.MediaLibraryProviderAdapter._candidate(
            {
                "source": "media_library",
                "candidate_kind": "derived_clip",
                "candidate_id": clip_id,
                "asset_id": None,
                "source_asset_id": "mla_parent",
                "source_clip_id": clip_id,
                "source_version": "a" * 64,
                "content_sha256": "b" * 64,
                "display_name": "化橘红倒入玻璃碗中",
                "preview_url": "/api/session-tasks/1/raw/clip.mp4",
                "duration_ms": 4240,
                "orientation": "portrait",
                "tags": ["化橘红", "玻璃碗"],
                "score": 1,
                "raw_score": 250,
                "score_reasons": ["片段完整名称命中"],
                "matched_fragments": [],
            },
            shared_search_id="mls_1700000000000_shared",
            provider_rank=1,
        )
        self.assertEqual(adapted["candidate_kind"], "derived_clip")
        self.assertEqual(adapted["candidate_id"], clip_id)
        self.assertEqual(adapted["provider_asset_id"], clip_id)
        self.assertIsNone(adapted["asset_id"])
        self.assertEqual(adapted["source_asset_id"], "mla_parent")
        self.assertEqual(adapted["source_clip_id"], clip_id)
        self.assertEqual(adapted["allowed_actions"], ["preview", "import_clip"])
        self.assertEqual(adapted["tags"], ["化橘红", "玻璃碗"])

        class FakeImportService:
            def __init__(self) -> None:
                self.calls = []

            def import_clip(self, asset_id, received_clip_id, request, **kwargs):
                self.calls.append(
                    (asset_id, received_clip_id, request, kwargs)
                )
                return {
                    "ok": True,
                    "import_id": "mli_agent_clip",
                    "status": "completed",
                    "reused": False,
                    "source_version": "a" * 64,
                    "item": {
                        "id": "storyboard_asset_mli_agent_clip",
                        "path": "SessionOutput/storyboard/assets/videos/clip.mp4",
                        "kind": "video",
                        "source": "media_library_clip",
                    },
                }

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {
                "id": 27,
                "session_id": 31,
                "workspace_dir": str(workspace),
            }
            run = {
                "schema_version": "koubo_asset_search_run_0.1",
                "search_id": "search_5001_clip",
                "task_id": 27,
                "session_id": 31,
                "created_at": services.now_ms(),
                "candidates": [adapted],
            }
            write_json(
                workspace
                / "SessionContext/AssetSearchAgent/SearchRuns/search_5001_clip.json",
                run,
            )
            imported_service = FakeImportService()
            sn = SimpleNamespace(
                ctx=SimpleNamespace(
                    media_library_import_service=imported_service
                ),
                workspace_for=lambda _task: workspace,
                read_json=read_json,
                write_json=write_json,
                add_event=lambda *_args, **_kwargs: None,
                load_plan=lambda _task, **_kwargs: ({"shots": []}, {}),
                redact_secret_text=lambda value: str(value),
            )
            with patch.dict(
                os.environ,
                {"OPENCREW_MEDIA_LIBRARY_CLIP_SEARCH_V1": "1"},
            ):
                result = asyncio.run(
                    services.import_asset_search_candidates(
                        task,
                        {
                            "search_id": "search_5001_clip",
                            "candidate_ids": [clip_id],
                        },
                        sc=sn,
                    )
                )
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["imported"][0]["source_kind"], "media_library_clip")
        self.assertEqual(result["imported"][0]["source_clip_id"], clip_id)
        self.assertEqual(imported_service.calls[0][0:2], ("mla_parent", clip_id))
        self.assertEqual(
            imported_service.calls[0][3],
            {"search_candidate_kind": "derived_clip"},
        )

    def test_media_library_import_uses_authoritative_copy_service_without_provider_network(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeImportService:
            def __init__(self) -> None:
                self.calls: list[tuple[str, object]] = []

            def import_original(self, asset_id, request):
                self.calls.append((asset_id, request))
                return {
                    "ok": True,
                    "import_id": "mli_agent_1",
                    "status": "completed",
                    "reused": False,
                    "source_version": "b" * 64,
                    "item": {
                        "id": "storyboard_asset_mli_agent_1",
                        "path": "SessionOutput/storyboard/assets/videos/global.mp4",
                        "kind": "video",
                        "source": "media_library_original",
                    },
                }

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 27, "session_id": 31, "workspace_dir": str(workspace)}
            run = {
                "schema_version": "koubo_asset_search_run_0.1",
                "search_id": "search_5000_abcd",
                "task_id": 27,
                "session_id": 31,
                "created_at": services.now_ms(),
                "candidates": [
                    {
                        "candidate_id": "mla_global_1",
                        "provider": "media_library",
                        "provider_asset_id": "mla_global_1",
                        "asset_id": "mla_global_1",
                        "source_version": "b" * 64,
                        "media_type": "video",
                        "title": "全局原片",
                        "media_library_search_id": "mls_1700000000000_shared",
                        "allowed_actions": [
                            "preview",
                            "open_editor",
                            "import_original",
                        ],
                        "import_supported": True,
                    }
                ],
            }
            write_json(
                workspace
                / "SessionContext/AssetSearchAgent/SearchRuns/search_5000_abcd.json",
                run,
            )
            imported_service = FakeImportService()
            sn = SimpleNamespace(
                ctx=SimpleNamespace(
                    media_library_import_service=imported_service
                ),
                workspace_for=lambda _task: workspace,
                read_json=read_json,
                write_json=write_json,
                add_event=lambda *_args, **_kwargs: None,
                load_plan=lambda _task, **_kwargs: ({"shots": []}, {}),
                redact_secret_text=lambda value: str(value),
            )
            with (
                patch.object(
                    services,
                    "provider_for",
                    side_effect=AssertionError(
                        "media_library import must not use provider_for"
                    ),
                ),
                patch.object(
                    services,
                    "_refresh_stale_candidate",
                    side_effect=AssertionError(
                        "media_library import must not refresh a URL"
                    ),
                ),
                patch.object(
                    services.asset_search_providers,
                    "download_candidate_file",
                    side_effect=AssertionError(
                        "media_library import must not download"
                    ),
                ),
            ):
                result = asyncio.run(
                    services.import_asset_search_candidates(
                        task,
                        {
                            "search_id": "search_5000_abcd",
                            "candidate_ids": ["mla_global_1"],
                            "dialogue_asset_key": "dialogue_0005",
                        },
                        sc=sn,
                    )
                )

        self.assertEqual(result["failed"], [])
        self.assertEqual(len(result["imported"]), 1)
        self.assertTrue(result["imported"][0]["global_media_library"])
        self.assertEqual(
            result["imported"][0]["source_kind"],
            "media_library_original",
        )
        self.assertEqual(len(imported_service.calls), 1)
        asset_id, request = imported_service.calls[0]
        self.assertEqual(asset_id, "mla_global_1")
        self.assertEqual(request.target_task_id, 27)
        self.assertEqual(request.search_id, "mls_1700000000000_shared")
        self.assertEqual(request.dialogue_asset_key, "dialogue_0005")
        self.assertTrue(
            request.idempotency_key.startswith("agent-asset-search:")
        )

    def test_planner_uses_prompt_async_then_polls_messages(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeClient:
            def __init__(self) -> None:
                self.prompt_calls = 0
                self.message_calls = 0

            def prompt_async(self, session_id, prompt, **kwargs) -> None:
                self.prompt_calls += 1
                self.session_id = session_id
                self.prompt = prompt
                self.kwargs = kwargs

            def messages(self, session_id, limit=120):
                self.message_calls += 1
                if self.message_calls < 2:
                    return []
                return [{"text": json.dumps({
                    "request_id": self.prompt.split('"request_id": "', 1)[1].split('"', 1)[0],
                    "media_types": ["video"],
                    "aspect": "16:9",
                    "sources": ["pexels"],
                    "queries": [{"query": "doctor tablet hospital corridor", "language": "en", "media_type": "video", "priority": 1}],
                })}]

        client = FakeClient()

        sn = SimpleNamespace(safe_session=lambda _session_id: {"opencode_session_id": "oc_123", "workspace_dir": "/tmp"}, resolve_model=lambda *_args, **_kwargs: ({"providerID": "openai", "modelID": "gpt-test"}, {}), opencode_client_for=lambda _session_row, **_sc_kwargs: client, last_completed_assistant=lambda messages, _started: messages[-1]["text"] if messages else None, load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {}), add_event=lambda *_args, **_kwargs: None)
        with (
            patch.object(services, "_sc", sn, create=True),
            patch.object(services, "read_asset_search_settings", lambda _task, **_sc_kwargs: services.default_asset_search_settings()),
            patch.object(services.time, "sleep", lambda _seconds: None),
        ):
            plan = services._build_asset_search_plan_sync({"id": 5, "session_id": 6}, {"text": "医院走廊医生平板", "media_types": ["video"]}, sc=sn)

        self.assertEqual(client.prompt_calls, 1)
        self.assertGreaterEqual(client.message_calls, 2)
        self.assertEqual(plan["queries"][0]["query"], "doctor tablet hospital corridor")
        self.assertFalse(plan["degraded"])

    def test_import_manifest_response_idempotency_and_origin_media_type(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.io_utils import safe_workspace_rel

        async def fake_download(_provider_id, _url, target_path: Path, **_kwargs):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\xff\xd8\xff\xe0jpeg")
            return {"content_sha256": "hash-image-1", "bytes": 8, "content_type": "image/jpeg", "final_url": _url, "redirect_chain": []}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            run = {
                "schema_version": "koubo_asset_search_run_0.1",
                "search_id": "search_1000_abcd",
                "task_id": 5,
                "session_id": 6,
                "created_at": services.now_ms(),
                "candidates": [{
                    "candidate_id": "pexels_image_123",
                    "provider": "pexels",
                    "provider_asset_id": "123",
                    "media_type": "image",
                    "title": "Doctor tablet",
                    "download_url": "https://images.pexels.com/photos/123/photo.jpg",
                    "source_url": "https://www.pexels.com/photo/123/",
                    "mime_type": "image/jpeg",
                    "import_supported": True,
                    "license": {"requires_attribution": False, "license_status": "confirmed", "name": "Pexels License"},
                    "creator": {"name": "Creator"},
                }],
            }
            write_json(workspace / "SessionContext/AssetSearchAgent/SearchRuns/search_1000_abcd.json", run)

            def upsert(workspace_arg: Path, asset: dict[str, object], **_sc_kwargs) -> None:
                manifest_path = workspace_arg / "SessionOutput/storyboard/koubo_storyboard_assets.json"
                payload = read_json(manifest_path)
                assets = [item for item in payload.get("assets", []) if item.get("path") != asset.get("path")]
                assets.append(asset)
                write_json(manifest_path, {"assets": assets})

            sn = SimpleNamespace(workspace_for=lambda _task: workspace, read_json=read_json, write_json=write_json, safe_workspace_rel=safe_workspace_rel, upsert_asset_manifest_item=upsert, add_event=lambda *_args, **_kwargs: None, load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {"manual_assets": []}))
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services.asset_search_providers, "download_candidate_file", fake_download),
            ):
                first = asyncio.run(services.import_asset_search_candidates(task, {"search_id": "search_1000_abcd", "candidate_ids": ["pexels_image_123"], "confirm_license": True}, sc=sn))
                second = asyncio.run(services.import_asset_search_candidates(task, {"search_id": "search_1000_abcd", "candidate_ids": ["pexels_image_123"], "confirm_license": True}, sc=sn))

            manifest = read_json(workspace / "SessionOutput/storyboard/koubo_storyboard_assets.json")

        self.assertTrue(first["ok"])
        self.assertIn("task", first)
        self.assertIn("meta", first)
        self.assertIn("plan", first)
        self.assertEqual(first["imported"][0]["origin"]["media_type"], "image")
        self.assertFalse(first["imported"][0]["skipped"])
        self.assertTrue(second["imported"][0]["skipped"])
        self.assertEqual(len(manifest["assets"]), 1)

    def test_import_returns_empty_storyboard_payload_when_source_is_not_ready(self) -> None:
        from fastapi import HTTPException

        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.io_utils import safe_workspace_rel

        async def fake_download(_provider_id, _url, target_path: Path, **_kwargs):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\xff\xd8\xff\xe0jpeg")
            return {
                "content_sha256": "hash-image-no-storyboard",
                "bytes": 8,
                "content_type": "image/jpeg",
                "final_url": _url,
                "redirect_chain": [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {
                "id": 5,
                "session_id": 6,
                "workspace_dir": str(workspace),
            }
            run = {
                "schema_version": "koubo_asset_search_run_0.1",
                "search_id": "search_1001_nostoryboard",
                "task_id": 5,
                "session_id": 6,
                "created_at": services.now_ms(),
                "candidates": [{
                    "candidate_id": "pexels_image_456",
                    "provider": "pexels",
                    "provider_asset_id": "456",
                    "media_type": "image",
                    "title": "Office interview",
                    "download_url": "https://images.pexels.com/photos/456/photo.jpg",
                    "source_url": "https://www.pexels.com/photo/456/",
                    "mime_type": "image/jpeg",
                    "import_supported": True,
                    "license": {
                        "requires_attribution": False,
                        "license_status": "confirmed",
                        "name": "Pexels License",
                    },
                    "creator": {"name": "Creator"},
                }],
            }
            write_json(
                workspace
                / "SessionContext/AssetSearchAgent/SearchRuns"
                / "search_1001_nostoryboard.json",
                run,
            )

            def upsert(
                workspace_arg: Path,
                asset: dict[str, object],
                **_sc_kwargs,
            ) -> None:
                manifest_path = (
                    workspace_arg
                    / "SessionOutput/storyboard/koubo_storyboard_assets.json"
                )
                payload = read_json(manifest_path)
                assets = [
                    item
                    for item in payload.get("assets", [])
                    if item.get("path") != asset.get("path")
                ]
                assets.append(asset)
                write_json(manifest_path, {"assets": assets})

            def missing_plan(_task, **_sc_kwargs):
                raise HTTPException(
                    status_code=404,
                    detail="Analysis V1 StoryBoard output not found",
                )

            sn = SimpleNamespace(
                workspace_for=lambda _task: workspace,
                read_json=read_json,
                write_json=write_json,
                safe_workspace_rel=safe_workspace_rel,
                upsert_asset_manifest_item=upsert,
                add_event=lambda *_args, **_kwargs: None,
                load_plan=missing_plan,
                empty_asset_library_payload=lambda _task, _workspace, **_kwargs: {
                    "ok": True,
                    "plan": {"shots": []},
                    "meta": {
                        "storyboard_ready": False,
                        "uploaded_images": [],
                    },
                },
            )
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(
                    services.asset_search_providers,
                    "download_candidate_file",
                    fake_download,
                ),
            ):
                result = asyncio.run(services.import_asset_search_candidates(
                    task,
                    {
                        "search_id": "search_1001_nostoryboard",
                        "candidate_ids": ["pexels_image_456"],
                        "confirm_license": True,
                    },
                    sc=sn,
                ))

        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"], {"shots": []})
        self.assertFalse(result["meta"]["storyboard_ready"])
        self.assertEqual(len(result["imported"]), 1)

    def test_import_rejects_unsupported_missing_attribution_unknown_candidate_and_prefix(self) -> None:
        from fastapi import HTTPException
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        self.assertEqual(services._license_import_error({"license": {"requires_attribution": True, "attribution_text": ""}}), "attribution_required_but_missing")
        with self.assertRaises(HTTPException):
            services.asset_search_validate_import_prefix("SessionOutput/storyboard/not-assets/file.jpg", "image")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            run = {
                "schema_version": "koubo_asset_search_run_0.1",
                "search_id": "search_2000_abcd",
                "task_id": 5,
                "session_id": 6,
                "created_at": services.now_ms(),
                "candidates": [{
                    "candidate_id": "wikimedia_video_1",
                    "provider": "wikimedia",
                    "provider_asset_id": "1",
                    "media_type": "video",
                    "import_supported": False,
                    "import_unsupported_reason": "Wikimedia video import is not supported in P0",
                }],
            }
            write_json(workspace / "SessionContext/AssetSearchAgent/SearchRuns/search_2000_abcd.json", run)
            event_names: list[str] = []
            sn = SimpleNamespace(workspace_for=lambda _task: workspace, read_json=read_json, write_json=write_json, add_event=lambda _session_id, name, _payload: event_names.append(name), load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {}))
            with patch.object(services, "_sc", sn, create=True):
                result = asyncio.run(services.import_asset_search_candidates(task, {"search_id": "search_2000_abcd", "candidate_ids": ["wikimedia_video_1", "missing"]}, sc=sn))

        self.assertEqual(result["failed"][0]["reason"], "import_not_supported")
        self.assertEqual(result["failed"][1]["reason"], "candidate_not_found")
        self.assertIn("koubo_storyboard.asset_search.import.failed", event_names)
        self.assertIn("koubo_storyboard.asset_search.import.completed", event_names)

    def test_stale_candidate_refreshes_url_before_download(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.io_utils import safe_workspace_rel

        downloaded_urls: list[str] = []

        class FakeProvider:
            async def refresh_candidate(self, candidate):
                return {**candidate, "download_url": "https://images.pexels.com/photos/123/fresh.jpg"}

        async def fake_download(_provider_id, url, target_path: Path, **_kwargs):
            downloaded_urls.append(url)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\xff\xd8\xff\xe0jpeg")
            return {"content_sha256": "hash-image-2", "bytes": 8, "content_type": "image/jpeg", "final_url": url, "redirect_chain": []}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            run = {
                "search_id": "search_3000_abcd",
                "task_id": 5,
                "session_id": 6,
                "created_at": services.now_ms() - services.ASSET_SEARCH_DEFAULT_TTL_MS - 1000,
                "candidates": [{
                    "candidate_id": "pexels_image_123",
                    "provider": "pexels",
                    "provider_asset_id": "123",
                    "media_type": "image",
                    "title": "Old",
                    "download_url": "https://images.pexels.com/photos/123/old.jpg",
                    "mime_type": "image/jpeg",
                    "import_supported": True,
                    "license": {"requires_attribution": False, "license_status": "confirmed"},
                }],
            }
            write_json(workspace / "SessionContext/AssetSearchAgent/SearchRuns/search_3000_abcd.json", run)
            sn = SimpleNamespace(workspace_for=lambda _task: workspace, read_json=read_json, write_json=write_json, safe_workspace_rel=safe_workspace_rel, upsert_asset_manifest_item=lambda ws, asset, **_sc_kwargs: write_json(ws / "SessionOutput/storyboard/koubo_storyboard_assets.json", {"assets": [asset]}), add_event=lambda *_args, **_kwargs: None, load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {}))
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "provider_for", lambda *_args, **_kwargs: FakeProvider()),
                patch.object(services.asset_search_providers, "download_candidate_file", fake_download),
            ):
                result = asyncio.run(services.import_asset_search_candidates(task, {"search_id": "search_3000_abcd", "candidate_ids": ["pexels_image_123"]}, sc=sn))

        self.assertEqual(downloaded_urls, ["https://images.pexels.com/photos/123/fresh.jpg"])
        self.assertEqual(result["failed"], [])

    def test_stale_candidate_refresh_revalidates_license_before_download(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services

        class FakeProvider:
            async def refresh_candidate(self, candidate):
                return {
                    **candidate,
                    "download_url": "https://images.pexels.com/photos/123/fresh.jpg",
                    "license": {"requires_attribution": True, "attribution_text": "", "license_status": "confirmed"},
                }

        async def fake_download(*_args, **_kwargs):
            raise AssertionError("download should not run after refreshed license fails")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = {"id": 5, "session_id": 6, "workspace_dir": str(workspace)}
            run = {
                "search_id": "search_3001_abcd",
                "task_id": 5,
                "session_id": 6,
                "created_at": services.now_ms() - services.ASSET_SEARCH_DEFAULT_TTL_MS - 1000,
                "candidates": [{
                    "candidate_id": "pexels_image_123",
                    "provider": "pexels",
                    "provider_asset_id": "123",
                    "media_type": "image",
                    "title": "Old",
                    "download_url": "https://images.pexels.com/photos/123/old.jpg",
                    "mime_type": "image/jpeg",
                    "import_supported": True,
                    "license": {"requires_attribution": False, "license_status": "confirmed"},
                }],
            }
            write_json(workspace / "SessionContext/AssetSearchAgent/SearchRuns/search_3001_abcd.json", run)
            sn = SimpleNamespace(workspace_for=lambda _task: workspace, read_json=read_json, write_json=write_json, add_event=lambda *_args, **_kwargs: None, load_plan=lambda _task, **_sc_kwargs: ({"shots": []}, {}))
            with (
                patch.object(services, "_sc", sn, create=True),
                patch.object(services, "provider_for", lambda *_args, **_kwargs: FakeProvider()),
                patch.object(services.asset_search_providers, "download_candidate_file", fake_download),
            ):
                result = asyncio.run(services.import_asset_search_candidates(task, {"search_id": "search_3001_abcd", "candidate_ids": ["pexels_image_123"]}, sc=sn))

        self.assertEqual(result["imported"], [])
        self.assertEqual(result["failed"][0]["reason"], "attribution_required_but_missing")

    def test_download_validation_rejects_extension_and_header_media_mismatch(self) -> None:
        from opcrew_backend.koubo.koubo_storyboard import asset_search_services as services
        from opcrew_backend.koubo.koubo_storyboard.asset_search_providers import AssetSearchProviderError

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "image.jpg"
            image_path.write_bytes(b"\xff\xd8\xff\xe0jpeg")
            video_path = Path(tmp) / "video.jpg"
            video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

            services._validate_downloaded_file(image_path, "image", "image/jpeg", "image/jpeg", 1024, ".jpg")
            with self.assertRaises(AssetSearchProviderError):
                services._validate_downloaded_file(image_path, "image", "image/jpeg", "image/jpeg", 1024, ".mp4")
            with self.assertRaises(AssetSearchProviderError):
                services._validate_downloaded_file(video_path, "image", "image/jpeg", "application/octet-stream", 1024, ".jpg")

    def test_frontend_search_agent_contract(self) -> None:
        api_source = KOUBO_API_PATH.read_text(encoding="utf-8")
        overlay_source = OVERLAY_PATH.read_text(encoding="utf-8")
        sidebar_source = SIDEBAR_PATH.read_text(encoding="utf-8")
        workspace_source = WORKSPACE_PATH.read_text(encoding="utf-8")
        panel_source = PANEL_PATH.read_text(encoding="utf-8")

        for token in (
            '"search-agent"',
            "SearchAgentWorkspace",
            "SearchAgentPanel",
            "createSearchAgentController",
            "onAssetLibraryResult",
        ):
            self.assertIn(token, overlay_source)
        self.assertIn("素材检索", sidebar_source)
        self.assertIn('<FlowIcon name="search" />', sidebar_source)
        for token in (
            "assetLibrarySearchSettings",
            "assetLibrarySearchPlan",
            "assetLibrarySearchStoryboardPlan",
            "streamAssetLibrarySearch",
            "importAssetLibrarySearch",
            "exportAssetLibrarySearchSourceList",
            "fetch(new URL(`api/koubo-storyboard/tasks/${taskId}/asset-library-search/search/events`",
            "getReader()",
        ):
            self.assertIn(token, api_source)
        self.assertNotIn("EventSource", api_source)
        for token in (
            "Search Brief",
            "Source",
            "Candidate Results",
            "Selected to Import",
            "Imported Results",
            "Provider Settings",
            "添加关键词",
            "导出来源清单",
            "audio",
            "local",
            "unsplash",
            "license_status",
            "source_url",
            "SearchEmptyState",
            "ual-search-card-actions",
            "Wikimedia 对长描述",
            'role="alertdialog"',
            "currentPlan?.edited ? { ...currentPlan, sources: selectedSources",
        ):
            self.assertIn(token, workspace_source)
        self.assertIn("const MAX_IMPORT_SELECTION = 12", workspace_source)
        self.assertIn("const finalItems = asArray(event.items || event.candidates)", workspace_source)
        self.assertIn("setCandidates(finalItems)", workspace_source)
        self.assertIn("setSelectedIds((previous) => new Set([...previous].filter((key) => finalKeys.has(key))))", workspace_source)
        self.assertIn("else if (next.size < MAX_IMPORT_SELECTION) next.add(key)", workspace_source)
        self.assertIn("Agent 过程状态", panel_source)
        self.assertIn("按 StoryBoard 批量", panel_source)
        self.assertNotIn("window.confirm", workspace_source)


if __name__ == "__main__":
    unittest.main()

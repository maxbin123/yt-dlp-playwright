from typing import Dict, Optional, Union
import os
import re
import hashlib
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError

class PlaywrightManifestIE(InfoExtractor):
    IE_NAME = 'playwright_manifest'
    _VALID_URL = r'.+'

    # Timeouts & constants
    TIMEOUT_RESPONSE    = 15_000
    TIMEOUT_PAGE_LOAD   = 20_000
    HLS_MANIFEST_PATTERN = re.compile(r'\.m3u8(?:$|\?)')

    def _real_initialize(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise ExtractorError(
                'Playwright not installed; run `pip install playwright && playwright install`.',
                expected=True)

    def _get_proxy_config(self) -> Optional[Dict[str, str]]:
        raw = self._downloader.params.get('proxy')
        if not raw:
            return None
        p = urlparse(raw)
        return {
            'server':   f'{p.scheme}://{p.hostname}:{p.port}',
            'username': p.username,
            'password': p.password,
        }

    def _ensure_state(self, login_url: str, state_file: str) -> str:
        if os.path.isfile(state_file):
            return state_file
        self.to_screen(f'⚙ {state_file!r} not found, launching headful login…')
        proxy_cfg = self._get_proxy_config()
        with sync_playwright() as pw:
            # headful so you can log in manually
            browser = pw.firefox.launch(headless=False)
            ctx     = browser.new_context(proxy=proxy_cfg)
            page    = ctx.new_page()
            page.goto(login_url, wait_until='networkidle')
            input("👉 Log in, then hit ENTER here…")
            ctx.storage_state(path=state_file)
            self.to_screen(f'✅ Wrote storage state to {state_file!r}')
            browser.close()
        return state_file

    def _real_extract(self, url: str) -> Dict[str, Union[str, list]]:
        # 1) ID = first 8 chars of MD5(url)
        video_id   = hashlib.md5(url.encode()).hexdigest()[:8]
        # 2) Ensure we have state.json (else interactive login)
        state_file = self._ensure_state(url, 'state.json')
        # 3) Build proxy + launch headless Firefox
        proxy_cfg  = self._get_proxy_config()
        with sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            ctx     = browser.new_context(
                         storage_state=state_file,
                         proxy=proxy_cfg,
                     )
            page = ctx.new_page()

            # 4) Wait for the HLS master playlist response
            with page.expect_response(
                lambda r: bool(self.HLS_MANIFEST_PATTERN.search(r.url)),
                timeout=self.TIMEOUT_RESPONSE
            ) as ev:
                page.goto(url, wait_until='domcontentloaded', timeout=self.TIMEOUT_PAGE_LOAD)
            manifest_url = ev.value.url
            if not manifest_url:
                raise ExtractorError('Could not detect HLS manifest')

            # 5) Grab the <title> for a human title
            title = page.title().strip() or video_id

            browser.close()

        # 6) Parse out all the MP4‐based HLS variants
        formats = self._extract_m3u8_formats(
            manifest_url,
            video_id,
        )

        return {
            'id':      video_id,
            'title':   title,
            'formats': formats,
        }
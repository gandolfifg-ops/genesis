from __future__ import annotations

import asyncio
import json
from pathlib import Path

from school_secretary.config import Settings, get_settings

ONQ_HOME = "/d2l/home"
LOGIN_WAIT_MESSAGE = """
A Chromium window should now be open on https://onq.queensu.ca/d2l/home

1. Complete Queen's NetID SSO in that window (username, password, any MFA).
2. Wait until you can see your onQ homepage (course tiles / the Brightspace navbar).
3. Return to this terminal and press Enter.

Cookies are then written to {session_path} and the persistent profile is kept in
{profile_dir} for later headless refreshes.
""".strip()


def cookies_from_session(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {cookie["name"]: cookie["value"] for cookie in data.get("cookies", [])}


async def _login_async(settings: Settings) -> Path:
    from playwright.async_api import async_playwright

    settings.ensure_dirs()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    url = settings.onq_base_url.rstrip("/") + ONQ_HOME
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        print(
            LOGIN_WAIT_MESSAGE.format(
                session_path=settings.session_path,
                profile_dir=settings.browser_profile_dir,
            )
        )
        await asyncio.to_thread(input, "Press Enter after you can see the onQ homepage...")
        await context.storage_state(path=str(settings.session_path))
        await context.close()
    print(f"Saved session cookies to {settings.session_path}")
    return settings.session_path


def login(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return asyncio.run(_login_async(settings))


async def _headless_context(settings: Settings):
    from playwright.async_api import async_playwright

    if not settings.session_path.exists():
        raise FileNotFoundError(
            f"No session file at {settings.session_path}. "
            "Run `uv run school-secretary login` once with a visible browser."
        )
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(settings.browser_profile_dir),
        headless=True,
        storage_state=str(settings.session_path),
        viewport={"width": 1280, "height": 900},
    )
    return playwright, context


async def download_with_dom(page, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with page.expect_download() as pending:
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception:
            # Navigation may abort when the download starts; that is OK.
            pass
    download = await pending.value
    await download.save_as(str(dest))
    return dest


async def scrape_pdf_links(page, course_url: str) -> list[str]:
    await page.goto(course_url, wait_until="domcontentloaded")
    hrefs = await page.eval_on_selector_all(
        "a[href]",
        """els => els.map(e => e.href).filter(h =>
            h && (h.toLowerCase().includes('.pdf') || h.toLowerCase().includes('download'))
        )""",
    )
    return list(dict.fromkeys(hrefs))

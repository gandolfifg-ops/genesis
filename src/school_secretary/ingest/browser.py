from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from school_secretary.config import Settings, get_settings

ONQ_HOME = "/d2l/home"
LOGIN_WAIT_MESSAGE = """
A real Chromium window should now be open on the Queen's onQ login page
(https://onq.queensu.ca/d2l/home).

1. Type your Queen's NetID and password.
2. Approve Duo MFA when prompted.
3. Wait until you can see the onQ homepage (course tiles / Brightspace navbar).
4. Return to this terminal and press Enter.

Playwright storage state is saved to {storage_state_path}
(and copied to {session_copy} so older readers still work).
The persistent profile stays in {profile_dir}. Headless ingest uses those
cookies afterward so Duo is not prompted on every refresh.
""".strip()


def cookies_from_session(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {cookie["name"]: cookie["value"] for cookie in data.get("cookies", [])}


def _write_storage_state_copy(settings: Settings, source: Path) -> None:
    copy = settings.session_json_copy_path
    if source.resolve() == copy.resolve():
        return
    shutil.copyfile(source, copy)


async def _login_async(settings: Settings) -> Path:
    from playwright.async_api import async_playwright

    settings.ensure_dirs()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.storage_state_path
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
                storage_state_path=dest,
                session_copy=settings.session_json_copy_path,
                profile_dir=settings.browser_profile_dir,
            )
        )
        await asyncio.to_thread(input, "Press Enter after you can see the onQ homepage...")
        await context.storage_state(path=str(dest))
        await context.close()
    _write_storage_state_copy(settings, dest)
    print(f"Saved Playwright storage state to {dest}")
    print(f"Copied to {settings.session_json_copy_path} as well.")
    return dest


def login(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return asyncio.run(_login_async(settings))


async def _headless_context(settings: Settings):
    from playwright.async_api import async_playwright

    state = settings.session_path
    if not state.exists():
        raise FileNotFoundError(
            f"No storage state at {settings.storage_state_path}. "
            "Run `uv run school-secretary login` once with a visible browser "
            "(NetID, password, Duo). Headless ingest will reuse that JSON so MFA "
            "is not prompted every time."
        )
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(settings.browser_profile_dir),
        headless=True,
        storage_state=str(state),
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

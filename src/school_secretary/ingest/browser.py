from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

from school_secretary.config import Settings, get_settings

ONQ_HOME = "/d2l/home"
LOGIN_WAIT_MESSAGE = """
A real Chromium window should now be open on the Queen's onQ login page
(https://onq.queensu.ca/d2l/home).

Run this on YOUR machine (Windows WSL), not a remote cloud desktop:

1. Type your Queen's NetID and password.
2. Approve Duo MFA when prompted.
3. Wait until you can see the onQ homepage (course tiles / Brightspace navbar).
4. Return to this terminal and press Enter.

Playwright storage state is saved to {storage_state_path}
(and copied to {session_copy} so older readers still work).
The persistent profile stays in {profile_dir}. After you press Enter, this same
window crawls content, announcements, and dropbox folders, downloads files into
data/raw/, then embeds them before Chromium closes. You do not download PDFs by hand.
""".strip()


def _print_wsl_display_hint() -> None:
    proc = Path("/proc/version")
    is_wsl = proc.exists() and "microsoft" in proc.read_text(encoding="utf-8", errors="ignore").lower()
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if is_wsl and not display:
        print("WSL: DISPLAY is unset, so Chromium may not appear.")
        print("On Windows 11, WSLg usually provides a display. In this same Ubuntu terminal:")
        print("  echo $DISPLAY")
        print("  export DISPLAY=:0")
        print("Then re-run: uv run school-secretary login")


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
    _print_wsl_display_hint()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            headless=False,
            accept_downloads=True,
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
        _write_storage_state_copy(settings, dest)
        print(f"Saved Playwright storage state to {dest}")
        print(f"Copied to {settings.session_json_copy_path} as well.")
        print("Crawling content, dropbox, and announcements — downloading files automatically...")
        from school_secretary.db.session import init_db
        from school_secretary.ingest.brightspace import IngestError, ingest_live_from_context

        try:
            init_db(settings)
            counts = await ingest_live_from_context(settings, context, page)
            print(counts)
        except IngestError as exc:
            print(str(exc))
            print("Session was saved. Refresh later with: uv run school-secretary ingest --live")
        finally:
            await context.close()
    return dest


def login(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return asyncio.run(_login_async(settings))


async def _open_persistent_context(settings: Settings, *, headless: bool):
    from playwright.async_api import async_playwright

    state = settings.session_path
    if not state.exists():
        raise FileNotFoundError(
            f"No storage state at {settings.storage_state_path}. "
            "Run `uv run school-secretary login` once with a visible browser "
            "(NetID, password, Duo). Live ingest reuses data/browser/ in a headed window."
        )
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            headless=headless,
            accept_downloads=True,
            viewport={"width": 1280, "height": 900},
        )
        await _overlay_storage_state(context, state)
        return playwright, context
    except Exception:
        await playwright.stop()
        raise


async def _overlay_storage_state(context, state_path: Path) -> None:
    data = json.loads(state_path.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    if not cookies:
        return
    try:
        await context.add_cookies(cookies)
    except Exception:
        # Persistent profile from headed login is enough; do not log cookie errors.
        return


def sanitize_filename(name: str) -> str:
    base = Path(name or "download.bin").name
    cleaned = re.sub(r"[^\w.\- ()]+", "_", base).strip("._")
    return (cleaned or "download.bin")[:180]


async def save_download(download, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / sanitize_filename(download.suggested_filename or "download.bin")
    await download.save_as(str(dest))
    return dest


async def click_and_save_download(page, locator, dest_dir: Path, timeout: int = 8_000) -> Path | None:
    try:
        async with page.expect_download(timeout=timeout) as pending:
            await locator.click(timeout=min(timeout, 4_000))
        return await save_download(await pending.value, dest_dir)
    except Exception:
        return None


async def download_page_files(page, dest_dir: Path, limit: int = 20) -> dict[str, Path]:
    """Click in-page download/file links. Never uses a scripted API client."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Path] = {}
    selectors = [
        "a[href$='.pdf' i]",
        "a[href*='.pdf?' i]",
        "a[href$='.docx' i]",
        "a[href*='.docx?' i]",
        "a[href$='.doc' i]",
        "a[href$='.pptx' i]",
        "a[href$='.xlsx' i]",
        "a[href*='download' i]",
        "a[href*='FileDownload' i]",
        "a[href*='downloadFile' i]",
        "a[href*='getFile' i]",
        "a[download]",
        "d2l-button:has-text('Download')",
        "button:has-text('Download')",
        "a:has-text('Download')",
        "a:has-text('.pdf')",
        "a:has-text('.docx')",
    ]
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = await loc.count()
        except Exception:
            continue
        for index in range(min(count, limit)):
            path = await click_and_save_download(page, loc.nth(index), dest_dir)
            if path is not None and path.exists():
                saved[path.name] = path
            if len(saved) >= limit:
                return saved
    hrefs = await _file_hrefs_on_page(page)
    for link in hrefs:
        if "/d2l/api/le/" in link or "/d2l/api/lp/" in link:
            continue
        filename = sanitize_filename(link.rsplit("/", 1)[-1].split("?")[0] or "download.pdf")
        dest = dest_dir / filename
        if dest.exists():
            saved[filename] = dest
            continue
        try:
            await download_with_dom(page, link, dest)
            if dest.exists():
                saved[dest.name] = dest
        except Exception:
            continue
        if len(saved) >= limit:
            break
    return saved


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


async def _file_hrefs_on_page(page) -> list[str]:
    """Collect file URLs from the current DOM. Does not navigate."""
    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => e.href).filter(h => {
                if (!h) return false;
                const lower = h.toLowerCase();
                return (
                    lower.includes('.pdf') ||
                    lower.includes('.docx') ||
                    lower.includes('.doc') ||
                    lower.includes('.pptx') ||
                    lower.includes('.xlsx') ||
                    lower.includes('download') ||
                    lower.includes('filedownload') ||
                    lower.includes('getfile')
                );
            })""",
        )
    except Exception:
        return []
    return list(dict.fromkeys(hrefs or []))


async def scrape_pdf_links(page, course_url: str) -> list[str]:
    if course_url and (page.url or "").rstrip("/") != course_url.rstrip("/"):
        await page.goto(course_url, wait_until="domcontentloaded")
    return await _file_hrefs_on_page(page)

"""Browser end-to-end tests. Run with:  pytest tests/e2e  (needs Microsoft Edge or Chrome installed)."""

import json
import os
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from PIL import Image  # noqa: E402

from scribeos.app import ServerThread  # noqa: E402
from scribeos.config import Paths  # noqa: E402
from scribeos.server.context import AppContext  # noqa: E402

CHANNEL = os.environ.get("SCRIBEOS_E2E_BROWSER", "msedge")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    ctx = AppContext(Paths(base))
    ctx.config.update({"storage": {"projectsDir": str(base / "Projects")}})
    st = ServerThread(ctx)
    st.start()
    st.wait_started()
    ctx.start_background()
    yield ctx
    ctx.shutdown()
    st.stop()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel=CHANNEL, headless=True)
        except Exception as e:  # pragma: no cover
            pytest.skip(f"browser not available: {e}")
        yield b
        b.close()


def watch_errors(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    return errors


def test_full_workflow(server, browser, tmp_path):
    ctx = server
    base = f"http://127.0.0.1:{ctx.local_port}"
    owner_ctx = browser.new_context(accept_downloads=True, bypass_csp=True)
    page = owner_ctx.new_page()
    errors = watch_errors(page)
    page.goto(f"{base}/auth?t={ctx.local_token}")

    # Welcome screen → new project (browser mode asks for name, then folder).
    page.get_by_role("button", name="New project").click()
    page.locator(".modal input").fill("E2E Novel")
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name="OK").click()
    page.wait_for_selector("#app:not([hidden])")
    assert page.locator("#projectName").inner_text() == "E2E Novel"

    # Write, format, autosave.
    editor = page.locator(".ProseMirror")
    editor.click()
    page.keyboard.type("It was a dark and stormy night. ")
    page.keyboard.press("Control+b")
    page.keyboard.type("Bold words")
    page.keyboard.press("Control+b")
    page.wait_for_selector("#saveState.ok", timeout=10000)
    cid = ctx.project.meta.activeChapterId
    saved = ctx.project.get_chapter(cid)["doc"]
    para = saved["content"][0]["content"]
    assert para[0]["text"].startswith("It was a dark") and para[-1]["marks"] == [{"type": "bold"}]

    # Chapter title edit is persisted.
    page.locator("#chapterTitle").fill("The Storm")
    page.wait_for_timeout(1200)
    assert ctx.project.meta.find_chapter(cid).title == "The Storm"

    # Insert an animated GIF (> 2 MB) through the toolbar.
    big = tmp_path / "big.gif"
    frames = [Image.effect_noise((900, 900), 80 + i * 10).convert("P") for i in range(3)]
    frames[0].save(big, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert big.stat().st_size > 2 * 1024 * 1024
    with page.expect_file_chooser() as fc:
        page.locator('button[title^="Insert image"]').click()
    fc.value.set_files(str(big))
    page.wait_for_selector(".ProseMirror figure img", timeout=20000)
    page.wait_for_selector("#saveState.ok", timeout=10000)
    assert any(n["type"] == "image" for n in ctx.project.get_chapter(cid)["doc"]["content"])

    # Themes: cycle with the keyboard shortcut, and pick Manga from the popover.
    page.keyboard.press("Control+Shift+T")
    page.wait_for_function("document.documentElement.dataset.theme === 'light'")
    page.locator("#btnTheme").click()
    page.locator(".popover").get_by_text("Manga").click()
    page.wait_for_function("document.documentElement.dataset.theme === 'manga'")
    assert ctx.config.settings.editor.theme == "manga"

    # Settings: font change applies immediately.
    page.keyboard.press("Control+,")
    page.locator(".setting-row", has_text="Body font").locator("select").select_option("EB Garamond")
    page.wait_for_function("getComputedStyle(document.querySelector('.ProseMirror')).fontFamily.includes('EB Garamond')")
    page.keyboard.press("Escape")

    # New chapter + sidebar.
    page.keyboard.press("Control+n")
    page.wait_for_function("document.querySelectorAll('.tree-chapter').length === 2")

    # Codex entry → mention highlighted in text.
    page.locator("#btnCodex").click()
    page.locator(".panel-body").get_by_role("button", name="New").click()
    page.locator(".modal .field", has_text="Name").locator("input").fill("Kaelith")
    page.locator(".modal").get_by_role("button", name="Save").click()
    page.locator(".ProseMirror").click()
    page.keyboard.type("Kaelith walked in.")
    page.wait_for_selector(".codex-mention", timeout=5000)
    page.keyboard.press("Escape")

    # Command palette → snapshot panel.
    page.keyboard.press("Control+k")
    page.locator(".palette-input").fill("snapshots")
    page.keyboard.press("Enter")
    page.wait_for_selector(".panel-header h3:has-text('Snapshots')")
    page.keyboard.press("Escape")

    # Export a PDF (browser download).
    page.keyboard.press("Control+e")
    with page.expect_download() as dl:
        page.locator(".modal").get_by_role("button", name="Export…").click()
    path = dl.value.path()
    assert Path(path).read_bytes()[:4] == b"%PDF"

    # Scratchpad note.
    page.keyboard.press("Alt+n")
    page.get_by_role("button", name="Add note").click()
    page.locator(".sticky-note textarea").last.fill("Plot hole here")
    page.wait_for_timeout(1200)
    assert any(n["text"] == "Plot hole here" for n in ctx.project.get_notes())

    assert not [e for e in errors if "favicon" not in e], errors
    owner_ctx.close()


def test_teamwork_live_editing(server, browser):
    ctx = server
    if ctx.project is None:
        ctx.create_project(Path(ctx.config.settings.storage.projectsDir), "Team Novel")
    base = f"http://127.0.0.1:{ctx.local_port}"
    host_ctx = browser.new_context(bypass_csp=True)
    host = host_ctx.new_page()
    host_errors = watch_errors(host)
    host.goto(f"{base}/auth?t={ctx.local_token}")
    host.wait_for_selector("#app:not([hidden])")
    host.wait_for_selector(".ProseMirror")

    # Start Teamwork from the panel.
    host.locator("#btnTeam").click()
    host.locator(".panel-body .field", has_text="Password").locator("input").fill("e2e-secret-pass")
    host.get_by_role("button", name="Start session").click()
    host.wait_for_selector(".live-banner")
    info = ctx.team.info(True)
    host.wait_for_selector("#saveState.live", timeout=10000)

    # Guest joins via the invite link in another browser profile.
    team_url = f"http://127.0.0.1:{info['port']}/join?room={info['roomCode']}"
    guest_ctx = browser.new_context(bypass_csp=True)
    guest = guest_ctx.new_page()
    guest.goto(team_url)
    guest.locator("#name").fill("Aylin")
    guest.locator("#password").fill("wrong-password")
    guest.locator("#joinBtn").click()
    guest.wait_for_selector("#joinError:has-text('Wrong')")
    guest_errors = watch_errors(guest)  # the failed attempt above logs an expected 403
    guest.locator("#password").fill("e2e-secret-pass")
    guest.locator("#joinBtn").click()
    guest.wait_for_selector("#app:not([hidden])")
    guest.wait_for_selector("#saveState.live", timeout=10000)

    # Guest types → host sees it live, with the guest's cursor label.
    guest.locator(".ProseMirror").click()
    guest.keyboard.press("Control+End")
    guest.keyboard.type(" Written together.")
    host.wait_for_function("document.querySelector('.ProseMirror')?.innerText.includes('Written together.')", timeout=10000)
    host.wait_for_selector(".collaboration-carets__label:has-text('Aylin')", timeout=10000)
    # Host types → guest sees it.
    host.locator(".ProseMirror").click()
    host.keyboard.press("Control+End")
    host.keyboard.type(" Host line.")
    guest.wait_for_function("document.querySelector('.ProseMirror')?.innerText.includes('Host line.')", timeout=10000)

    # Chat.
    guest.locator("#btnTeam").click()
    guest.locator(".chat-form input").fill("hi from guest")
    guest.locator(".chat-form button").click()
    host.wait_for_selector(".chat-msg:has-text('hi from guest')", timeout=10000)
    assert "null" not in host.locator("#sidePanel").inner_text()

    # Guest can't see host-only UI.
    assert guest.locator("#btnStats").is_hidden()

    # End session: guest is signed out; text was saved on the host's disk.
    host.get_by_role("button", name="End session").click()
    host.locator(".modal").get_by_role("button", name="End session").click()
    guest.wait_for_function("document.body.innerText.includes('disconnected') || document.body.innerText.includes('ended')", timeout=15000)
    cid = ctx.project.meta.activeChapterId
    text = json.dumps(ctx.project.get_chapter(cid)["doc"])
    assert "Written together." in text and "Host line." in text
    host.wait_for_selector("#saveState:not(.live)", timeout=10000)

    assert not [e for e in host_errors if "favicon" not in e and "WebSocket" not in e], host_errors
    assert not [e for e in guest_errors if "favicon" not in e and "WebSocket" not in e and "401" not in e and "403" not in e], guest_errors
    host_ctx.close()
    guest_ctx.close()


def test_every_screen_opens_without_errors(server, browser, tmp_path):
    ctx = server
    if ctx.project is None:
        ctx.create_project(Path(ctx.config.settings.storage.projectsDir), "Sweep Novel")
    from scribeos.storage import pmdoc

    cid = ctx.project.meta.chapters()[0].id
    ctx.project.save_chapter(cid, pmdoc.text_to_doc("A dragon slept. The dragon woke."), snapshot_interval=float("inf"))
    ctx.project.create_snapshot(cid, "before")
    ctx.project.save_chapter(cid, pmdoc.text_to_doc("A dragon slept. The dragon flew away."), snapshot_interval=float("inf"))
    ctx.project.update_meta({"activeChapterId": cid})

    bc = browser.new_context(bypass_csp=True, accept_downloads=True)
    page = bc.new_page()
    errors = watch_errors(page)
    page.goto(f"http://127.0.0.1:{ctx.local_port}/auth?t={ctx.local_token}")
    page.wait_for_selector(".ProseMirror")

    # Settings: every tab renders.
    page.keyboard.press("Control+,")
    for tab in ("Appearance", "Writing", "Fonts", "Storage & backups", "Voice", "Teamwork", "Shortcuts", "About"):
        page.locator(".settings-tab", has_text=tab).click()
        page.wait_for_timeout(250)
    page.locator(".settings-tab", has_text="Appearance").click()
    page.locator(".theme-card", has_text="Custom").click()
    page.wait_for_selector(".custom-theme")
    page.locator(".theme-card", has_text="Book").click()
    page.wait_for_function("document.documentElement.dataset.theme === 'book'")
    page.locator(".settings-tab", has_text="Storage").click()
    page.get_by_role("button", name="Back up now").click()
    page.wait_for_selector(".backup-item")
    page.keyboard.press("Escape")

    # Snapshots: preview shows a diff, restore works.
    page.locator("#btnSnapshot").click()
    page.locator(".snap-item", has_text="before").click()
    page.wait_for_selector(".diff-del")
    page.locator(".modal").get_by_role("button", name="Restore").click()
    page.locator(".modal").last.get_by_role("button", name="Restore").click()
    page.wait_for_function("document.querySelector('.ProseMirror')?.innerText.includes('The dragon woke.')")
    page.keyboard.press("Escape")

    # Search → jump → replace all.
    page.keyboard.press("Control+Shift+F")
    page.locator(".panel-body input").first.fill("dragon")
    page.wait_for_selector(".result")
    page.locator(".result").first.click()
    page.locator(".replace-row input").fill("wyvern")
    page.get_by_role("button", name="Replace all").click()
    page.locator(".modal").get_by_role("button", name="Replace all").click()
    page.wait_for_function("document.querySelector('.ProseMirror')?.innerText.includes('wyvern')")
    page.keyboard.press("Escape")

    # Corkboard, statistics, templates, media library, trash, export preview, palette, project menu.
    page.locator("#btnCorkboard").click()
    page.wait_for_selector(".index-card")
    page.locator(".index-card textarea").first.fill("A synopsis")
    page.keyboard.press("Escape")
    page.locator("#btnStats").click()
    page.wait_for_selector(".heatmap .hm-cell:not(.empty)")
    page.keyboard.press("Escape")
    page.locator("#btnTemplates").click()
    page.locator(".template-card", has_text="Kishōtenketsu").get_by_role("button", name="Insert here").click()
    page.wait_for_function("document.querySelector('.ProseMirror')?.innerText.includes('Kishōtenketsu')")
    page.keyboard.press("Control+k")
    page.locator(".palette-input").fill("media library")
    page.keyboard.press("Enter")
    page.wait_for_selector(".panel-header h3:has-text('Media library')")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+e")
    page.get_by_role("button", name="Word For editors").click()
    page.get_by_role("button", name="PDF Print-ready").click()
    page.get_by_role("button", name="Preview first pages").click()
    page.wait_for_selector(".preview-page", timeout=30000)
    page.keyboard.press("Escape")
    page.locator("#btnProjectMenu").click()
    page.locator(".popover").get_by_text("Trash").click()
    page.wait_for_selector(".modal h3:has-text('Trash')")
    page.keyboard.press("Escape")

    # Focus / typewriter / zen toggles.
    page.locator("#btnFocus").click()
    page.wait_for_selector("body.focus-mode")
    page.locator("#btnTypewriter").click()
    page.keyboard.press("F11")
    page.wait_for_selector("body.zen")
    page.keyboard.press("F11")

    # Import a Markdown document into the project.
    md = tmp_path / "import.md"
    md.write_text("# Imported One\n\nText one.\n\n# Imported Two\n\nText two.\n", encoding="utf-8")
    page.locator("#btnProjectMenu").click()
    with page.expect_file_chooser() as fc:
        page.locator(".popover").get_by_text("Import document").click()
    fc.value.set_files(str(md))
    page.wait_for_selector(".tree-chapter:has-text('Imported Two')")

    assert not [e for e in errors if "favicon" not in e], errors
    bc.close()

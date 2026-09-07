from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
import time
from typing import Literal
from uuid import uuid4

from playwright.async_api import async_playwright, Error as PlaywrightError

from .runtime import (BrowserBusy, BrowserLock, debugger_url, browser_running,
                      launch_browser, load_session, save_session, default_project,
                      project_url as normalize_project_url, conversation_key)

EDITOR = "#prompt-textarea"
ASSISTANT = '[data-message-author-role="assistant"]'
STOP = '[data-testid="stop-button"], button[aria-label="Stop streaming"], button[aria-label="停止生成"]'
SEND = '[data-testid="send-button"], button[aria-label="Send prompt"], button[aria-label="发送提示"]'
LOGIN = 'button[data-testid="login-button"], a[href*="/auth/login"]'
Effort = Literal["instant", "medium", "high", "xhigh", "pro"]
EFFORTS = {"instant": (0, "即时"), "medium": (1, "中"), "high": (2, "高"),
           "xhigh": (3, "极高"), "pro": (4, "Pro")}
EFFORT_PICKER = '[data-testid="composer-intelligence-picker-content"]'
ATTACHMENT_TILES = 'form [role="group"][class*="file-tile"][aria-label]'


def attachment_paths(attachments: list[str] | None) -> list[Path]:
    paths = [Path(value).expanduser().resolve() for value in (attachments or [])]
    for path in paths:
        if not path.is_file():
            raise ValueError(f"附件不是可读取的普通文件：{path.name}")
        with path.open("rb"):
            pass
    if len({path.name for path in paths}) != len(paths):
        raise ValueError("同一轮附件的文件名不能重复，以便核对网页实际接收的文件。")
    return paths


async def upload_attachments(page, paths: list[Path], timeout: int) -> dict | None:
    """Upload through the composer and wait for every visible tile to finish."""
    try:
        await page.locator('form #upload-files[type="file"]').set_input_files(
            [str(path) for path in paths], timeout=10000)
        deadline = time.monotonic() + timeout
        # The website can rename a library collision, e.g. report(1).pdf.
        expected = [re.compile(re.escape(path.stem) + r"(?:\(\d+\))?" + re.escape(path.suffix) + r"\Z") for path in paths]
        while True:
            for alert in await page.locator('[role="alert"]').all():
                if await alert.is_visible() and (await alert.inner_text()).strip():
                    return {"status": "upload_failed", "message": "网页报告附件错误，尚未发送；请检查专用窗口。", "url": page.url}
            tiles = await page.locator(ATTACHMENT_TILES).evaluate_all("""nodes => nodes.map(e => ({
                name: e.getAttribute('aria-label'),
                pending: !!e.querySelector('.cursor-wait, [role="progressbar"], circle[stroke-dasharray]')
            }))""")
            unmatched = list(tiles)
            for pattern in expected:
                match = next((tile for tile in unmatched if pattern.fullmatch(tile["name"])), None)
                if match is not None:
                    unmatched.remove(match)
                else:
                    break
            else:
                if not unmatched and not any(tile["pending"] for tile in tiles):
                    return None
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.3)
    except PlaywrightError:
        pass
    return {"status": "upload_unconfirmed", "message": "附件上传未能确认完成，尚未发送。已选附件可能保留在网页，请检查后处理；不要自动重试。", "url": page.url}


async def select_effort(page, effort: Effort) -> dict:
    """Use the visible five-step capability slider, and verify before sending."""
    trigger = page.locator('button.__composer-pill[aria-haspopup="menu"]').filter(
        has_text=re.compile(r"^(即时|中|高|极高|(?:\d+\s*)?Pro)$"))
    try:
        await trigger.first.click(timeout=5000)
        picker = page.locator(EFFORT_PICKER)
        await picker.wait_for(state="visible", timeout=5000)
        slider = picker.locator('[role="slider"]')
        target, label = EFFORTS[effort]
        if await slider.get_attribute("aria-valuemin") != "0" or await slider.get_attribute("aria-valuemax") != "4":
            return {"status": "effort_unavailable", "message": "网页能力档位与已验证的五档布局不同，尚未发送。"}
        control = picker.locator('[role="menuitem"][aria-keyshortcuts="ArrowLeft ArrowRight"]')
        current = int(await slider.get_attribute("aria-valuenow"))
        while current != target:
            direction = -1 if target < current else 1
            await control.press("ArrowLeft" if direction < 0 else "ArrowRight", timeout=5000)
            current += direction
            await picker.locator(f'[role="slider"][aria-valuenow="{current}"]').wait_for(state="attached", timeout=5000)
        actual = " ".join((await picker.locator('[role="menuitem"][aria-expanded]').first.inner_text()).split())
        if actual != label and not (effort == "pro" and actual.endswith("Pro")):
            return {"status": "effort_unavailable", "message": "网页未确认请求的推理档位，尚未发送。"}
        return {"status": "selected", "effort": effort, "effort_label": actual}
    except (PlaywrightError, ValueError):
        return {"status": "effort_unavailable", "message": "无法选择所需推理档位。请检查网页可用档位或额度；尚未发送。"}
    finally:
        if not page.is_closed():
            await page.keyboard.press("Escape")


@asynccontextmanager
async def connection():
    async with async_playwright() as playwright:
        endpoint = debugger_url()
        if endpoint is None:
            raise PlaywrightError("Dedicated browser is not running")
        browser = await playwright.chromium.connect_over_cdp(endpoint, timeout=8000)
        yield browser.contexts[0]
        # Exiting Playwright disconnects its client; do not close the user's browser.


async def visible(page, selector: str) -> bool:
    locator = page.locator(selector)
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            return True
    return False


async def target_id(page) -> str:
    client = await page.context.new_cdp_session(page)
    try:
        result = await client.send("Target.getTargetInfo")
        return result["targetInfo"]["targetId"]
    finally:
        await client.detach()


async def ensure_browser() -> dict | None:
    if browser_running():
        return None
    result = await asyncio.to_thread(launch_browser)
    return None if result["status"] in {"launched", "already_running"} else result


async def find_page(context, session: dict):
    saved_key = conversation_key(session["url"])
    for page in context.pages:
        if not page.url.startswith("https://chatgpt.com/"):
            continue
        # A user can navigate the original tab elsewhere. Its target ID alone
        # is not enough to identify the saved conversation.
        if saved_key and conversation_key(page.url) == saved_key:
            session["target_id"] = await target_id(page)
            save_session(session)
            return page
        if not saved_key and await target_id(page) == session["target_id"]:
            if conversation_key(page.url):
                session["url"] = page.url
                save_session(session)
                return page
    if saved_key:
        page = await context.new_page()
        await page.goto(session["url"], wait_until="domcontentloaded")
        session["target_id"] = await target_id(page)
        save_session(session)
        return page
    return None


async def page_problem(page) -> dict | None:
    if await visible(page, ".error-code"):
        return {"status": "network_error", "message": "专用 Chrome 的网页连接失败；请检查网络，无需因此重新登录。"}
    if await visible(page, '#challenge-running, #challenge-stage, iframe[src*="challenges.cloudflare.com"]'):
        return {"status": "human_required", "message": "网页要求人工验证，请在专用窗口处理。"}
    if not page.url.startswith("https://chatgpt.com/") or await visible(page, LOGIN):
        return {"status": "login_required", "message": "已复用原浏览器配置，但网页仍要求登录。请在专用窗口登录一次；后续继续复用。"}
    return None


async def wait_ready(page) -> dict | None:
    try:
        await page.wait_for_function("""({editor, login}) => {
            const visible = selector => [...document.querySelectorAll(selector)].some(e => e.getClientRects().length);
            return location.origin !== 'https://chatgpt.com' || visible(editor) || visible(login)
                || visible('#challenge-running, #challenge-stage, .error-code');
        }""", arg={"editor": EDITOR, "login": LOGIN}, timeout=60000)
    except PlaywrightError:
        return {"status": "editor_unavailable", "message": "输入框尚未可用，请检查专用窗口的网络、弹窗或页面布局。"}
    return await page_problem(page)


async def snapshot(page, baseline: int, after_id: str | None = None, answer_id: str | None = None) -> dict:
    """Only read the answer for this turn; an old answer is never a result."""
    problem = await page_problem(page)
    if problem:
        return problem
    bubbles = page.locator(ASSISTANT)
    text = ""
    finished_controls = False
    ids = await bubbles.evaluate_all("nodes => nodes.map(e => e.getAttribute('data-message-id'))")
    index = None
    if answer_id:
        if answer_id in ids:
            index = ids.index(answer_id)
    elif after_id and ids and ids[-1]:
        if ids[-1] != after_id:
            index = len(ids) - 1
    elif len(ids) > baseline:
        index = len(ids) - 1
    if index is not None:
        last = bubbles.nth(index)
        text = await last.inner_text()
        finished_controls = await last.evaluate("""element => {
            const turn = element.closest('article') || element.closest('[data-testid^="conversation-turn-"]');
            return !!turn?.querySelector('[data-testid="copy-turn-action-button"], button[aria-label="Copy"], button[aria-label="复制"]');
        }""")
    streaming = await visible(page, STOP)
    if text.strip() and finished_controls and not streaming:
        return {"status": "completed", "answer": text, "answer_id": ids[index]}
    alerts = page.locator('[role="alert"]')
    for index in range(await alerts.count()):
        alert = alerts.nth(index)
        if await alert.is_visible():
            message = (await alert.inner_text()).strip()
            if message:
                return {"status": "needs_attention", "message": message[:600], "partial_answer": text}
    return {"status": "running", "partial_answer": text}


async def wait_for_answer(page, session: dict, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        if page.is_closed():
            return {"status": "tab_closed", "message": "标签页已关闭。用同一 session_id 调用 read_chatgpt，可按保存的会话地址恢复。"}
        if conversation_key(page.url) and page.url != session["url"]:
            session["url"] = page.url
            save_session(session)
        state = await snapshot(page, session["baseline"], session.get("after_id"), session.get("answer_id"))
        if state["status"] != "running":
            if state.get("answer_id"):
                session["answer_id"] = state["answer_id"]
            session["status"] = state["status"]
            save_session(session)
            return state
        if time.monotonic() >= deadline:
            session["status"] = "running"
            save_session(session)
            return {**state, "message": "等待已到时，网页可能仍在生成。用 read_chatgpt(session_id) 继续等待，不要重新提交。"}
        await asyncio.sleep(min(1.5, max(0, deadline - time.monotonic())))


def response(session: dict, state: dict, started: float) -> dict:
    return {"session_id": session["session_id"], "url": session["url"],
            "project_url": session.get("project_url"),
            "effort": session.get("effort"), "effort_label": session.get("effort_label"),
            **{key: value for key, value in state.items() if key != "answer_id"},
            "elapsed_seconds": round(time.monotonic() - started, 1)}


async def ask(prompt: str, session_id: str | None = None, timeout: int = 600, project_url: str | None = None, effort: Effort = "xhigh", attachments: list[str] | None = None) -> dict:
    if not prompt.strip():
        return {"status": "invalid_input", "message": "prompt 不能为空。"}
    if not 1 <= timeout <= 840:
        return {"status": "invalid_input", "message": "timeout 必须为 1–840 秒。"}
    if effort not in EFFORTS:
        return {"status": "invalid_input", "message": "effort 可选 instant、medium、high、xhigh、pro；默认 xhigh。"}
    try:
        paths = attachment_paths(attachments)
        selected_project = normalize_project_url(project_url) if project_url else None
    except (ValueError, OSError) as error:
        return {"status": "invalid_input", "message": str(error)}
    started = time.monotonic()
    session = None
    try:
        with BrowserLock():
            if session_id:
                session = load_session(session_id)
                if session is None:
                    return {"status": "unknown_session", "message": "找不到会话；请使用本工具返回的 session_id。"}
                if selected_project and selected_project != session.get("project_url"):
                    return {"status": "project_conflict", "message": "追问沿用原会话的项目。要使用另一项目，请新建会话。"}
            problem = await ensure_browser()
            if problem:
                return problem
            async with connection() as context:
                if session_id:
                    page = await find_page(context, session)
                    if page is None:
                        return response(session, {"status": "session_url_unavailable", "message": "上次关闭前未保存有效会话地址，无法自动恢复；不会重新发送原任务。"}, started)
                    problem = await wait_ready(page)
                    if problem:
                        return response(session, problem, started)
                    previous = await wait_for_answer(page, session, min(timeout, 25))
                    if previous["status"] != "completed":
                        if previous["status"] == "running":
                            previous = {"status": "previous_turn_unfinished", "message": "上一轮尚未确认完成。先用 read_chatgpt 读取结果。"}
                        return response(session, previous, started)
                else:
                    selected_project = selected_project or default_project()
                    start_url = selected_project or "https://chatgpt.com/"
                    # Reuse only an empty home tab for this project.
                    page = None
                    for candidate in context.pages:
                        if candidate.url.rstrip("/") == start_url.rstrip("/") and await candidate.locator(ASSISTANT).count() == 0:
                            page = candidate
                            break
                    if page is None:
                        page = await context.new_page()
                        await page.goto(start_url, wait_until="domcontentloaded")
                    problem = await wait_ready(page)
                    if problem:
                        return problem
                editor = page.locator(EDITOR)
                try:
                    await editor.wait_for(state="visible", timeout=12000)
                except PlaywrightError:
                    return {"status": "editor_unavailable", "message": "输入框不可用。请检查登录、弹窗或页面布局。"}
                if await visible(page, STOP):
                    return {"status": "busy", "message": "网页正在生成，请等待现有回复。"}
                if (await editor.inner_text()).strip() or await page.locator(ATTACHMENT_TILES).count():
                    return {"status": "draft_present", "message": "专用窗口有未发送草稿；请先处理，工具不会覆盖草稿。"}
                selected_effort = await select_effort(page, effort)
                if selected_effort["status"] != "selected":
                    return selected_effort
                if paths:
                    problem = await upload_attachments(page, paths, min(timeout, 120))
                    if problem:
                        return problem
                bubbles = page.locator(ASSISTANT)
                baseline = await bubbles.count()
                session = {"session_id": session_id or str(uuid4()), "target_id": await target_id(page),
                           "url": page.url, "baseline": baseline,
                           "after_id": await bubbles.last.get_attribute("data-message-id") if baseline else None,
                           "project_url": session.get("project_url") if session_id else selected_project,
                           "effort": effort, "effort_label": selected_effort["effort_label"],
                           "status": "sending"}
                save_session(session)
                await editor.fill(prompt)
                send = page.locator(SEND).first
                if await send.count():
                    await send.click(timeout=10000)
                else:
                    await editor.press("Enter")
                # Never retry a send automatically, including after ambiguous failure.
                session["status"] = "running"
                save_session(session)
                state = await wait_for_answer(page, session, timeout)
                return response(session, state, started)
    except BrowserBusy:
        return {"status": "busy", "message": "已有一个 MCP/CLI 请求正在使用专用浏览器，请稍后读取或再提交。"}
    except PlaywrightError:
        state = {"status": "browser_error", "message": "浏览器连接或页面操作失败。若已开始发送，请先读取当前会话，避免重复提交。"}
        return response(session, state, started) if session else state


async def read(session_id: str, timeout: int = 600) -> dict:
    if not 0 <= timeout <= 840:
        return {"status": "invalid_input", "message": "timeout 必须为 0–840 秒。"}
    session = load_session(session_id)
    if session is None:
        return {"status": "unknown_session", "message": "找不到会话。"}
    started = time.monotonic()
    try:
        with BrowserLock():
            problem = await ensure_browser()
            if problem:
                return response(session, problem, started)
            async with connection() as context:
                page = await find_page(context, session)
                if page is None:
                    return response(session, {"status": "session_url_unavailable", "message": "没有可恢复的会话地址；不会重新发送原任务。"}, started)
                problem = await wait_ready(page)
                if problem:
                    return response(session, problem, started)
                state = await wait_for_answer(page, session, timeout)
                return response(session, state, started)
    except BrowserBusy:
        return {"status": "busy", "message": "已有请求正在等待网页回复。"}
    except PlaywrightError:
        return response(session, {"status": "browser_error", "message": "读取浏览器失败，未重新发送任务。"}, started)


async def doctor() -> dict:
    if not browser_running():
        return {"status": "browser_not_running", "message": "运行 web-gpt browser。"}
    async with connection() as context:
        pages = [page for page in context.pages if page.url.startswith("https://chatgpt.com/")]
        if not pages:
            return {"status": "no_chatgpt_tab", "message": "专用 Chrome 已运行，但尚未打开 ChatGPT。ask/read 会自动打开或恢复页面；此状态不代表登录失效。"}
        page = pages[-1]
        problem = await page_problem(page)
        if problem:
            return problem
        if await visible(page, EDITOR):
            return {"status": "ready", "chatgpt_tabs": len(pages), "default_project_url": default_project(), "message": "已保存的登录态可用，输入框就绪。"}
        return {"status": "editor_unavailable", "message": "请处理专用窗口中的登录或提示页。"}

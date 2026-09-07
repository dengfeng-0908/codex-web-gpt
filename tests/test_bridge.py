import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.async_api import async_playwright

from web_gpt import chatgpt
from web_gpt.runtime import BrowserLock, load_session, set_default_project

PROJECT = "https://chatgpt.com/g/g-p-fixture/project"
OTHER_PROJECT = "https://chatgpt.com/g/g-p-other/project"

FIXTURE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="turns"></div>
<form onsubmit="return false">
<input id="upload-files" type="file" multiple>
<div id="attachments"></div>
<div id="prompt-textarea" contenteditable="true"></div>
<button type="button" class="__composer-pill" aria-haspopup="menu" id="effort-trigger">极高</button>
<button type="button" data-testid="send-button">Send</button>
</form>
<script>
window.uploadDelay = 500;
document.querySelector('#upload-files').onchange = event => {
 window.uploaded = [...event.target.files].map(f => ({name:f.name,size:f.size}));
 for (const file of event.target.files) {
   const tile = document.createElement('div');
   tile.className = 'group/file-tile'; tile.setAttribute('role','group');
   tile.setAttribute('aria-label',file.name);
   tile.innerHTML = '<span class="cursor-wait">uploading</span>';
   document.querySelector('#attachments').append(tile);
   setTimeout(() => {
     if (window.uploadError) {
       const alert = document.createElement('div'); alert.setAttribute('role','alert');
       alert.textContent = 'Upload rejected'; document.body.append(alert);
     } else {
       tile.innerHTML = '';
       if (window.renameUpload) tile.setAttribute('aria-label',file.name.replace(/(\\.[^.]+)$/, '(1)$1'));
     }
   }, window.uploadDelay);
 }
};
window.effortValue = Number(localStorage.getItem('effortValue') ?? 3);
const effortLabels = ['即时', '中', '高', '极高', '6 Pro'];
const effortTrigger = document.querySelector('#effort-trigger');
effortTrigger.textContent = effortLabels[window.effortValue];
effortTrigger.onclick = () => {
 const picker = document.createElement('div');
 picker.dataset.testid = 'composer-intelligence-picker-content';
 picker.innerHTML = '<div role="menuitem" aria-expanded="false" id="effort-label"></div><div role="menuitem" aria-keyshortcuts="ArrowLeft ArrowRight" tabindex="0"><span role="slider" aria-valuemin="0" aria-valuemax="4" style="display:none"></span></div>';
 document.body.append(picker);
 const update = () => {
   picker.querySelector('[role="slider"]').setAttribute('aria-valuenow', window.effortValue);
   picker.querySelector('#effort-label').textContent = effortLabels[window.effortValue];
   effortTrigger.textContent = effortLabels[window.effortValue];
   localStorage.setItem('effortValue', window.effortValue);
 };
 update();
 picker.querySelector('[aria-keyshortcuts]').onkeydown = event => {
   if (event.key === 'ArrowLeft') window.effortValue = Math.max(0, window.effortValue - 1);
   if (event.key === 'ArrowRight') window.effortValue = Math.min(4, window.effortValue + 1);
   update();
 };
};
document.addEventListener('keydown', event => {
 if (event.key === 'Escape') document.querySelector('[data-testid="composer-intelligence-picker-content"]')?.remove();
});
window.historyKey = location.pathname;
window.historyTurns = JSON.parse(localStorage.getItem(window.historyKey) || '[]');
window.sent = window.historyTurns.map(turn => turn.question);
window.responseDelay = 50;
function renderAnswer(turn, complete) {
 const article = document.createElement('article');
 const answer = document.createElement('div');
 answer.dataset.messageAuthorRole = 'assistant';
 answer.dataset.messageId = turn.id;
 answer.textContent = complete ? turn.answer : 'partial';
 article.append(answer);
 if (complete) {
   const copy = document.createElement('button');
   copy.dataset.testid = 'copy-turn-action-button'; copy.textContent = 'Copy';
   article.append(copy);
 }
 document.querySelector('#turns').append(article);
 return article;
}
window.historyTurns.forEach(turn => renderAnswer(turn, true));
document.querySelector('[data-testid="send-button"]').onclick = () => {
 const editor = document.querySelector('#prompt-textarea');
 window.sent.push(editor.innerText);
 const question = editor.innerText;
 window.sentWhileUploading = !!document.querySelector('#attachments .cursor-wait');
 window.sentAttachments = [...document.querySelectorAll('#attachments [role=group]')].map(e=>e.getAttribute('aria-label'));
 document.querySelector('#attachments').innerHTML = '';
 editor.innerText = '';
 if (!location.pathname.includes('/c/')) {
   const next = Number(localStorage.getItem('nextConversation') || 0) + 1;
   localStorage.setItem('nextConversation', next);
   const prefix = location.pathname.endsWith('/project') ? location.pathname.slice(0, -8) : '';
   window.historyKey = prefix + '/c/fixture-' + next;
   history.replaceState({}, '', window.historyKey);
 }
 const stop = document.createElement('button');
 stop.dataset.testid = 'stop-button'; stop.textContent = 'Stop';
 document.body.append(stop);
 const turn = {id: 'answer-' + window.sent.length, question, effort: window.effortValue,
               answer: 'Answer ' + window.sent.length + ': ' + question};
 const article = renderAnswer(turn, false);
 setTimeout(() => {
   article.querySelector('[data-message-author-role]').textContent = turn.answer;
   const copy = document.createElement('button');
   copy.dataset.testid = 'copy-turn-action-button'; copy.textContent = 'Copy';
   article.append(copy); stop.remove();
   window.historyTurns.push(turn);
   localStorage.setItem(window.historyKey, JSON.stringify(window.historyTurns));
 }, window.responseDelay);
};
</script></body></html>"""


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"WEB_GPT_DATA_DIR": self.storage.name})
        self.env.start()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(channel="chrome", headless=True, chromium_sandbox=True)
        self.context = await self.browser.new_context()
        await self.context.route("**/*", lambda route: route.fulfill(body=FIXTURE, content_type="text/html"))
        self.page = await self.context.new_page()
        await self.page.goto("https://chatgpt.com/")

        @asynccontextmanager
        async def connection():
            yield self.context

        self.connection = patch.object(chatgpt, "connection", connection)
        self.running = patch.object(chatgpt, "browser_running", return_value=True)
        self.connection.start()
        self.running_mock = self.running.start()

    async def asyncTearDown(self):
        self.running.stop()
        self.connection.stop()
        await self.browser.close()
        await self.playwright.stop()
        self.env.stop()
        self.storage.cleanup()

    async def test_send_and_follow_up_preserve_session(self):
        first = await chatgpt.ask("first question", timeout=5)
        self.assertEqual(first["status"], "completed")
        second = await chatgpt.ask("follow-up", first["session_id"], timeout=5)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(second["answer"], "Answer 2: follow-up")
        self.assertEqual(await self.page.evaluate("window.sent"), ["first question", "follow-up"])
        saved = load_session(first["session_id"])
        self.assertNotIn("prompt", saved)
        self.assertNotIn("answer", saved)

    async def test_timeout_then_read_does_not_send_twice(self):
        await self.page.evaluate("window.responseDelay = 2200")
        result = await chatgpt.ask("slow question", timeout=1)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["partial_answer"], "partial")
        complete = await chatgpt.read(result["session_id"], timeout=5)
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(await self.page.evaluate("window.sent.length"), 1)

    async def test_previous_answer_is_not_a_new_answer(self):
        await chatgpt.ask("first", timeout=5)
        state = await chatgpt.snapshot(self.page, baseline=1)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["partial_answer"], "")

    async def test_streaming_text_is_not_complete_even_with_copy_control(self):
        await self.page.evaluate("""() => {
          document.querySelector('#turns').innerHTML = '<article><div data-message-author-role="assistant">still streaming</div><button data-testid="copy-turn-action-button">Copy</button></article>';
          const stop = document.createElement('button'); stop.dataset.testid = 'stop-button'; document.body.append(stop);
        }""")
        state = await chatgpt.snapshot(self.page, baseline=0)
        self.assertEqual(state["status"], "running")

    async def test_draft_is_preserved(self):
        await self.page.locator(chatgpt.EDITOR).fill("user draft")
        result = await chatgpt.ask("new task", timeout=1)
        self.assertEqual(result["status"], "draft_present")
        self.assertEqual(await self.page.locator(chatgpt.EDITOR).inner_text(), "user draft")
        self.assertEqual(await self.page.evaluate("window.sent.length"), 0)

    async def test_parallel_process_lock_refuses_second_send(self):
        with BrowserLock():
            result = await chatgpt.ask("must not send", timeout=1)
        self.assertEqual(result["status"], "busy")
        self.assertEqual(await self.page.evaluate("window.sent.length"), 0)

    async def test_upload_waits_for_all_files_and_accepts_library_rename(self):
        files = [Path(self.storage.name) / name for name in ['note.txt', 'picture.png']]
        for path in files:
            path.write_bytes(b'fixture bytes')
        await self.page.evaluate('window.renameUpload = true')
        result = await chatgpt.ask('read files', timeout=5, attachments=[str(p) for p in files])
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(await self.page.evaluate('window.sentWhileUploading'))
        self.assertEqual(await self.page.evaluate('window.sentAttachments'), ['note(1).txt', 'picture(1).png'])
        followup = await chatgpt.ask('continue', result['session_id'], timeout=5)
        self.assertEqual(followup['status'], 'completed')
        self.assertEqual(await self.page.evaluate('window.sentAttachments'), [])

    async def test_upload_failure_and_timeout_never_send(self):
        path = Path(self.storage.name) / 'note.txt'
        path.write_text('test')
        await self.page.evaluate('window.uploadError = true')
        result = await chatgpt.ask('must not send', timeout=2, attachments=[str(path)])
        self.assertEqual(result['status'], 'upload_failed')
        self.assertEqual(await self.page.evaluate('window.sent'), [])
        await self.page.reload()
        await self.page.evaluate('window.uploadDelay = 5000')
        result = await chatgpt.ask('must not send', timeout=1, attachments=[str(path)])
        self.assertEqual(result['status'], 'upload_unconfirmed')
        self.assertEqual(await self.page.evaluate('window.sent'), [])
        again = await chatgpt.ask('keep draft', timeout=1)
        self.assertEqual(again['status'], 'draft_present')

    async def test_missing_attachment_rejected_before_browser_launch(self):
        self.running_mock.return_value = False
        with patch.object(chatgpt, 'launch_browser') as launch:
            result = await chatgpt.ask('invalid', attachments=[self.storage.name + '/missing.png'])
        self.assertEqual(result['status'], 'invalid_input')
        launch.assert_not_called()

    async def test_login_page_prevents_submission(self):
        await self.page.evaluate("""() => {
          const button = document.createElement('button');
          button.dataset.testid = 'login-button'; button.textContent = 'Login'; document.body.append(button);
        }""")
        result = await chatgpt.ask("must not send", timeout=1)
        self.assertEqual(result["status"], "login_required")
        self.assertEqual(await self.page.evaluate("window.sent.length"), 0)

    async def test_ask_starts_browser_when_closed(self):
        self.running_mock.return_value = False
        with patch.object(chatgpt, "launch_browser", return_value={"status": "launched"}) as launch:
            result = await chatgpt.ask("start automatically", timeout=5)
        self.assertEqual(result["status"], "completed")
        launch.assert_called_once()

    async def test_closed_tab_read_restores_without_resending(self):
        first = await chatgpt.ask("remember this", timeout=5)
        await self.page.close()
        self.running_mock.return_value = False
        with patch.object(chatgpt, "launch_browser", return_value={"status": "launched"}) as launch:
            restored = await chatgpt.read(first["session_id"], timeout=5)
        launch.assert_called_once()
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(restored["answer"], first["answer"])
        self.assertEqual(restored["url"], first["url"])
        self.assertEqual(await self.context.pages[-1].evaluate("window.sent"), ["remember this"])

    async def test_closed_tab_follow_up_restores_history(self):
        first = await chatgpt.ask("first", timeout=5)
        await self.page.close()
        second = await chatgpt.ask("next", first["session_id"], timeout=5)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["url"], first["url"])
        self.assertEqual(second["answer"], "Answer 2: next")
        self.assertEqual(await self.context.pages[-1].evaluate("window.sent"), ["first", "next"])

    async def test_repurposed_tab_is_not_used_for_follow_up(self):
        first = await chatgpt.ask("first", timeout=5)
        await self.page.goto("https://chatgpt.com/c/unrelated")
        second = await chatgpt.ask("next", first["session_id"], timeout=5)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["url"], first["url"])
        self.assertEqual(await self.page.evaluate("window.sent"), [])
        self.assertEqual(await self.context.pages[-1].evaluate("window.sent"), ["first", "next"])

    async def test_default_project_override_and_existing_chat_binding(self):
        set_default_project(PROJECT)
        first = await chatgpt.ask("first", timeout=5)
        self.assertEqual(first["project_url"], PROJECT)
        self.assertIn("/g/g-p-fixture/c/", first["url"])
        override = await chatgpt.ask("override", timeout=5, project_url=OTHER_PROJECT)
        self.assertEqual(override["project_url"], OTHER_PROJECT)
        self.assertIn("/g/g-p-other/c/", override["url"])
        set_default_project(OTHER_PROJECT)
        followup = await chatgpt.ask("next", first["session_id"], timeout=5)
        self.assertEqual(followup["project_url"], PROJECT)
        self.assertEqual(followup["url"], first["url"])
        conflict = await chatgpt.ask("must not send", first["session_id"], timeout=5, project_url=OTHER_PROJECT)
        self.assertEqual(conflict["status"], "project_conflict")
        page = next(page for page in self.context.pages if page.url == first["url"])
        self.assertEqual(await page.evaluate("window.sent"), ["first", "next"])

    async def test_saved_answer_id_survives_history_truncation(self):
        first = await chatgpt.ask("first", timeout=5)
        second = await chatgpt.ask("second", first["session_id"], timeout=5)
        await self.page.locator("article").first.evaluate("element => element.remove()")
        result = await chatgpt.read(first["session_id"], timeout=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], second["answer"])
        self.assertNotIn("answer_id", result)

    async def test_invalid_project_is_rejected_before_launch(self):
        self.running_mock.return_value = False
        with patch.object(chatgpt, "launch_browser") as launch:
            result = await chatgpt.ask("must not send", project_url="https://example.com/project")
        self.assertEqual(result["status"], "invalid_input")
        launch.assert_not_called()

    async def test_effort_is_selected_before_each_send(self):
        session_id = None
        for effort, (value, label) in chatgpt.EFFORTS.items():
            with self.subTest(effort=effort):
                result = await chatgpt.ask("test " + effort, session_id, timeout=5, effort=effort)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["effort"], effort)
                self.assertTrue(result["effort_label"].endswith(label))
                self.assertEqual(await self.page.evaluate("window.historyTurns.at(-1).effort"), value)
                session_id = result["session_id"]

    async def test_unavailable_effort_does_not_send_or_silently_fallback(self):
        await self.page.locator("#effort-trigger").click()
        await self.page.locator('[role="slider"]').evaluate("e => e.setAttribute('aria-valuemax', '3')")
        # Keep the visible, restricted picker when the bridge opens the trigger.
        await self.page.locator("#effort-trigger").evaluate("e => e.onclick = () => {}")
        result = await chatgpt.ask("must not send", timeout=5, effort="pro")
        self.assertEqual(result["status"], "effort_unavailable")
        self.assertEqual(await self.page.evaluate("window.sent"), [])
        self.assertEqual(await self.page.locator(chatgpt.EDITOR).inner_text(), "")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_tools_and_validation(self):
        executable = str(Path(__file__).resolve().parents[1] / ".venv/bin/web-gpt")
        async with stdio_client(StdioServerParameters(command=executable, args=["mcp"])) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                tools = await client.list_tools()
                self.assertEqual({tool.name for tool in tools.tools}, {"ask_chatgpt", "read_chatgpt"})
                ask_tool = next(tool for tool in tools.tools if tool.name == "ask_chatgpt")
                self.assertIn("project_url", ask_tool.inputSchema["properties"])
                self.assertIn("attachments", ask_tool.inputSchema["properties"])
                self.assertNotIn("project_url", ask_tool.inputSchema.get("required", []))
                self.assertEqual(ask_tool.inputSchema["properties"]["effort"]["default"], "xhigh")
                self.assertEqual(set(ask_tool.inputSchema["properties"]["effort"]["enum"]), set(chatgpt.EFFORTS))
                reply = await client.call_tool("ask_chatgpt", {"prompt": ""})
                self.assertFalse(reply.isError)
                payload = json.loads(reply.content[0].text)
                self.assertEqual(payload["status"], "invalid_input")


if __name__ == "__main__":
    unittest.main()

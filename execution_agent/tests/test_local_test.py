import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from threading import Event, Thread
import pytest
from execution_agent import local_test as local


def sample(url='https://example.test'):
    return {'schema_version': 1, 'name': 'My test', 'steps': [
        {'action': 'NAVIGATE', 'input_value': url, 'locators': []},
        {'action': 'TYPE', 'input_value': '{{PERSON}}', 'locators': [{'type': 'ID', 'value': 'missing'}, {'type': 'ID', 'value': 'name'}],
         'check': {'kind': 'VALUE_EQUALS', 'expected': '{{PERSON}}'}}]}


def test_save_open_and_settings_are_transient(tmp_path):
    path = tmp_path / 'test.json'; recording = sample()
    local.save_test(path, recording)
    assert local.load_test(path) == recording
    assert local.setting_names(recording) == ['PERSON']
    assert local.plan(recording, {'PERSON': 'private-value'})[1]['resolved_input'] == 'private-value'
    assert 'private-value' not in path.read_text()
    recording['name'] = 'Edited'; local.save_test(path, recording)
    assert local.load_test(path)['name'] == 'Edited'


def test_missing_settings_and_plain_password_rejected():
    with pytest.raises(ValueError, match='PERSON'): local.plan(sample(), {})
    recording = sample(); recording['steps'][1].update(is_secret=True, input_value='password')
    with pytest.raises(ValueError, match='Password'): local.validate(recording)


@pytest.fixture
def website():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
            self.wfile.write(b'<title>Example</title><input id="name"><button id="save">Save</button>')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}'
    finally: server.shutdown(); server.server_close(); thread.join()


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Browser runtime required')
def test_replay_checks_and_cancel(website, tmp_path):
    recording = sample(website)
    report = asyncio.run(local.run(recording, {'PERSON': 'Ada'}, tmp_path / 'pass', Event(), lambda *_: None, headless=True))
    assert report['status'] == 'COMPLETED'
    recording['steps'][1]['check']['expected'] = 'Wrong'
    report = asyncio.run(local.run(recording, {'PERSON': 'Ada'}, tmp_path / 'fail', Event(), lambda *_: None, headless=True))
    assert report['status'] == 'FAILED' and (tmp_path / 'fail' / 'step-2.png').exists()
    assert 'Ada' not in (tmp_path / 'fail' / 'results.json').read_text()
    stopped = Event(); stopped.set()
    report = asyncio.run(local.run(recording, {'PERSON': 'Ada'}, tmp_path / 'stop', stopped, lambda *_: None, headless=True))
    assert report['status'] == 'CANCELLED' and not report['steps']


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Browser runtime required')
def test_record_stop_then_replay(website, tmp_path, monkeypatch):
    sessions = []
    class Observed(local.RecordingSession):
        async def attach(self, context):
            self.context = context; sessions.append(self); await super().attach(context)
    monkeypatch.setattr(local, 'RecordingSession', Observed)
    stopped = Event()
    async def exercise():
        task = asyncio.create_task(local.record('Recorded', website, stopped, lambda *_: None, headless=True))
        try:
            async with asyncio.timeout(25):
                while not sessions or not sessions[0].context.pages: await asyncio.sleep(.05)
                await sessions[0].context.pages[0].locator('#name').fill('Ada')
                stopped.set()
                return await task
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    recording = asyncio.run(exercise())
    assert [s['action'] for s in recording['steps']] == ['NAVIGATE', 'TYPE']
    assert recording['steps'][1]['input_value'] == 'Ada'
    recording['steps'][1]['check'] = {'kind': 'VALUE_EQUALS', 'expected': 'Ada'}
    local.save_test(tmp_path / 'test.json', recording)
    report = asyncio.run(local.run(local.load_test(tmp_path / 'test.json'), {}, tmp_path / 'replay', Event(), lambda *_: None, headless=True))
    assert report['status'] == 'COMPLETED'


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Browser runtime required')
def test_cancel_interrupts_waiting_step(website, tmp_path):
    recording = sample(website)
    recording['steps'][1]['locators'] = [{'type': 'ID', 'value': 'does-not-exist'}]
    stopped = Event()
    async def exercise():
        def notify(kind, text):
            if kind == 'status' and text.startswith('Step 2'):
                asyncio.get_running_loop().call_later(.2, stopped.set)
        async with asyncio.timeout(15):
            return await local.run(recording, {'PERSON': 'Ada'}, tmp_path / 'cancel', stopped, notify, headless=True)
    report = asyncio.run(exercise())
    assert report['status'] == 'CANCELLED'
    assert len(report['steps']) == 1
    assert (tmp_path / 'cancel' / 'results.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows desktop smoke check')
def test_desktop_window_builds(monkeypatch):
    import tkinter as tk
    from execution_agent import standalone
    original = tk.Tk
    windows = []
    def create():
        root = original(); root.withdraw(); windows.append(root)
        def inspect():
            root.update_idletasks()
            assert 'Record and Run Tests' in root.title()
            assert root.winfo_children()
            root.destroy()
        root.mainloop = inspect
        return root
    monkeypatch.setattr(tk, 'Tk', create)
    standalone.main()
    assert len(windows) == 1


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Browser runtime required')
def test_submit_button_and_changing_element_id(tmp_path):
    from playwright.async_api import async_playwright
    from execution_agent.recorder import RecordingSession
    from execution_agent.worker import execute_step
    async def exercise():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel='msedge', headless=True)
            try:
                context = await browser.new_context()
                recording = RecordingSession('Login', 'https://example.test')
                await recording.attach(context)
                page = await context.new_page()
                async def load(suffix):
                    # Install the recorder after set_content, as init scripts run on navigation.
                    await page.set_content('''<form onsubmit="event.preventDefault(); document.querySelector('#folder').hidden=false">
                      <input id="username"><input id="password" type="password">
                      <input type="submit" id="login" value="Login"></form>
                      <button hidden id="folder" onclick="document.title='Opened'">Folders</button>''')
                    await page.locator('#folder').evaluate('(el, id) => { el.id = id; document.querySelector("form").onsubmit = e => {e.preventDefault(); el.hidden=false}; }', 'ember' + suffix)
                await load('123')
                from execution_agent.recorder import CAPTURE_SCRIPT
                await page.evaluate('window.__assuredenCapturing = false')
                await page.evaluate(CAPTURE_SCRIPT)
                await page.locator('#username').fill('Example')
                await page.locator('#password').fill('not-a-real-password')
                await page.locator('#login').click()
                await page.get_by_text('Folders', exact=True).click()
                await page.wait_for_timeout(200)
                steps = recording.data['steps'][1:]
                assert [s['action'] for s in steps] == ['TYPE', 'TYPE', 'CLICK', 'CLICK']
                assert any(x['value'] == 'login' for x in steps[2]['locators'])
                assert any(x.get('notes') == 'Visible text' and 'Folders' in x['value'] for x in steps[3]['locators'])
                assert all('ember123' not in x['value'] for x in steps[3]['locators'])
                assert 'not-a-real-password' not in str(recording.data)
                await load('999')
                compiled = local.plan(recording.data, {local.setting_names(recording.data)[0]: 'test-password'})
                for step in compiled[1:]:
                    result, _ = await execute_step(page, step)
                    assert result['status'] == 'PASSED', result
                assert await page.title() == 'Opened'
            finally:
                await browser.close()
    asyncio.run(exercise())


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Browser runtime required')
def test_many_element_alternatives_are_unique_and_ranked():
    from playwright.async_api import async_playwright
    from execution_agent.recorder import RecordingSession, CAPTURE_SCRIPT
    async def exercise():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel='msedge', headless=True)
            try:
                context = await browser.new_context()
                session = RecordingSession('Alternatives', 'https://example.test')
                await session.attach(context); page = await context.new_page()
                await page.set_content('''<section id="account"><label for="ember999">Account name</label>
                    <input id="ember999" data-testid="account-input" data-cy="account" name="account"
                    placeholder="Enter account" title="Account setting" aria-label="Account" class="account-field"></section>
                    <button class="same">Same</button><button class="same">Same</button>''')
                await page.evaluate('window.__assuredenCapturing = false'); await page.evaluate(CAPTURE_SCRIPT)
                await page.locator('input').fill('Ada'); await page.locator('input').blur()
                await page.locator('button').first.click(); await page.wait_for_timeout(100)
                locators = session.data['steps'][1]['locators']
                assert 8 <= len(locators) <= 16
                assert locators[0]['type'] == 'TEST_ID'
                assert len({(x['type'], x['value']) for x in locators}) == len(locators)
                assert all('ember999' not in x['value'] for x in locators)
                assert any(x['notes'] == 'Field label' for x in locators)
                # Ambiguous text/class choices must not be offered as safe alternatives.
                button_locators = session.data['steps'][2]['locators']
                assert not any(x['notes'] in ('Visible text', 'Element appearance') for x in button_locators)
                from execution_agent.worker import candidate_locator
                for item in locators:
                    matched = candidate_locator(page, {'strategy': item['type'], 'selector': item['value']})
                    assert await matched.count() == 1
                    assert await matched.evaluate('el => el.tagName') == 'INPUT'
            finally: await browser.close()
    asyncio.run(exercise())

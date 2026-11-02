"""Server-independent recording files and execution using the canonical browser engine."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from .recorder import RecordingSession
from .worker import execute_step, timestamp

ACTIONS = {"NAVIGATE": "Open website", "CLICK": "Click", "TYPE": "Enter text", "SELECT": "Choose option", "CHECK": "Tick box", "UNCHECK": "Untick box"}
CHECKS = {"": "No check", "VISIBLE": "Element is visible", "TEXT_EQUALS": "Text equals", "TEXT_CONTAINS": "Text contains", "VALUE_EQUALS": "Entered value equals", "URL_CONTAINS": "Page address contains", "TITLE_EQUALS": "Page title equals"}
FINDERS = {"ID", "CSS_SELECTOR", "XPATH", "ARIA_LABEL", "TEST_ID", "TEXT"}
VARIABLE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def validate(recording):
    if not isinstance(recording, dict) or recording.get('schema_version') != 1:
        raise ValueError('This recording file is not supported.')
    if not isinstance(recording.get('name'), str) or not recording['name'].strip():
        raise ValueError('Enter a test name.')
    steps = recording.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 500:
        raise ValueError('A test needs between 1 and 500 steps.')
    for step in steps:
        if not isinstance(step, dict) or step.get('action') not in ACTIONS:
            raise ValueError('This test contains an unsupported step.')
        if not isinstance(step.get('input_value', ''), str):
            raise ValueError('Step values must be text.')
        if step.get('is_secret') and not VARIABLE.fullmatch(step.get('input_value', '')):
            raise ValueError('Password steps must use a named setting, such as {{PASSWORD}}.')
        locators = step.get('locators', [])
        if not isinstance(locators, list) or len(locators) > 16:
            raise ValueError('Use at most 16 ways to find an element.')
        for locator in locators:
            if not isinstance(locator, dict) or locator.get('type') not in FINDERS or not isinstance(locator.get('value'), str) or not locator['value'].strip():
                raise ValueError('An element has an invalid way to find it.')
        if step['action'] != 'NAVIGATE' and not any(x.get('is_active', True) for x in locators):
            raise ValueError('Choose how to find the element for each step.')
        check = step.get('check', {})
        if not isinstance(check, dict) or check.get('kind', '') not in CHECKS or not isinstance(check.get('expected', ''), str):
            raise ValueError('This test contains an unsupported check.')
        if step['action'] == 'NAVIGATE' and check.get('kind') in {'VISIBLE', 'TEXT_EQUALS', 'TEXT_CONTAINS', 'VALUE_EQUALS'} and not locators:
            raise ValueError('An element check needs an element. Add it to an element step.')
    return deepcopy(recording)


def save_test(path, recording):
    data = {'format': 'assureden-local-test', 'version': 1, 'recording': validate(recording)}
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_test(path):
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ValueError('This file is too large. Choose a recording smaller than 2 MB.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict) and data.get('format') == 'assureden-local-test':
        if data.get('version') != 1:
            raise ValueError('This test file version is not supported.')
        data = data.get('recording')
    return validate(data)


def setting_names(recording):
    return sorted({key for s in recording['steps'] for text in (s.get('input_value', ''), s.get('check', {}).get('expected', '')) for key in VARIABLE.findall(text)})


def resolve(text, settings):
    # Values are used literally; settings never execute code or expand other settings.
    def replace(match):
        key = match.group(1)
        if key not in settings:
            raise ValueError('Enter a value for setting ' + key)
        return settings[key]
    return VARIABLE.sub(replace, text)


def plan(recording, settings, screenshots=True):
    recording = validate(recording)
    result = []
    for index, step in enumerate(recording['steps'], 1):
        locators = [x for x in step.get('locators', []) if x.get('is_active', True)]
        candidates = [{'strategy': x['type'], 'selector': x['value']} for x in locators]
        locator = {**candidates[0], 'fallbacks': candidates[1:]} if candidates else None
        value = resolve(step.get('input_value', ''), settings)
        if step['action'] == 'NAVIGATE':
            url = urlparse(value)
            if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
                raise ValueError('Use a website address beginning with http:// or https://, without a password in the address.')
        check = step.get('check', {})
        checks = []
        if check.get('kind'):
            checks.append({'assertion_id': str(uuid.uuid4()), 'assertion_type': check['kind'],
                           'expected_value': resolve(check.get('expected', ''), settings), 'is_fatal': True, 'locator': locator})
        result.append({'execution_step_id': str(uuid.uuid4()), 'step_id': str(index), 'step_version': 1,
            'position': index, 'action': step['action'], 'resolved_input': value, 'locator': locator,
            'timeout_ms': 10000, 'assertions': checks, 'screenshot_on_failure': screenshots})
    return result


async def record(name, url, stop, notify, *, channel='msedge', headless=False):
    recording = RecordingSession(name, url)
    plan(recording.data, {})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel=channel, headless=headless)
        try:
            context = await browser.new_context()
            await recording.attach(context)
            page = await context.new_page()
            await page.goto(url, timeout=15000)
            notify('status', 'Recording. Use the website, then choose Stop Recording.')
            while not stop.is_set() and browser.is_connected():
                await asyncio.sleep(.15)
            if browser.is_connected() and not page.is_closed():
                await page.evaluate('document.activeElement?.blur()')
                await page.wait_for_timeout(200)
        finally:
            await browser.close()
    return recording.data


async def run(recording, settings, folder, stop, notify, *, channel='msedge', headless=False, screenshots=True):
    steps = plan(recording, settings, screenshots)
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=False)
    report = {'test_name': recording['name'], 'started_at': timestamp(), 'status': 'FAILED', 'steps': []}
    failed = False
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=channel, headless=headless)
            try:
                page = await browser.new_page()
                for step in steps:
                    if stop.is_set():
                        report['status'] = 'CANCELLED'; break
                    notify('status', f"Step {step['position']} of {len(steps)}: {ACTIONS[step['action']]}")
                    task = asyncio.create_task(execute_step(page, step, skip=failed))
                    try:
                        while not task.done() and not stop.is_set():
                            await asyncio.sleep(.1)
                        if stop.is_set() and not task.done():
                            task.cancel(); await asyncio.gather(task, return_exceptions=True)
                            report['status'] = 'CANCELLED'; break
                        result, picture = await task
                    finally:
                        if not task.done():
                            task.cancel(); await asyncio.gather(task, return_exceptions=True)
                    if picture:
                        filename = f"step-{step['position']}.png"
                        (folder / filename).write_bytes(picture)
                        result['attached_file'] = filename
                    report['steps'].append(result)
                    notify('result', result)
                    failed |= result['status'] == 'FAILED'
                else:
                    report['status'] = 'FAILED' if failed else 'COMPLETED'
            finally:
                await browser.close()
    except Exception:
        report['status'] = 'FAILED'
        report['message'] = 'Could not finish the test. Check that Microsoft Edge is installed and the website is reachable.'
        raise
    finally:
        report['completed_at'] = timestamp()
        (folder / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report

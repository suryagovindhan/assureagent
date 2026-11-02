"""Windows desktop companion: one-time pairing, recording and execution."""
import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import threading
from urllib.parse import urlparse
import httpx
from playwright.async_api import async_playwright
from .recorder import RecordingSession
from .worker import AgentWorker, CAPABILITIES

CONFIG = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AssureDenAgent" / "connection.bin"
DESKTOP_CAPABILITIES = {**CAPABILITIES, "recording": True}


def protect(data, decrypt=False):
    """Bind the saved credential to the current Windows user using DPAPI."""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


def save_config(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_bytes(protect(json.dumps(value).encode()))
    temp.replace(CONFIG)


async def capture(client, request, channel="msedge", *, headless=False):
    recording = RecordingSession(request["name"], request["url"])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel=channel, headless=headless)
        try:
            context = await browser.new_context()
            await recording.attach(context)
            page = await context.new_page()
            await page.goto(request["url"])
            while browser.is_connected():
                heartbeat = await client.post('/api/agents/heartbeat-extended', json=DESKTOP_CAPABILITIES)
                heartbeat.raise_for_status()
                result = await client.get(f"/api/agent-control/recordings/{request['id']}/state")
                result.raise_for_status()
                status = result.json()["status"]
                if status == "STOP_REQUESTED":
                    if not page.is_closed():
                        await page.evaluate("document.activeElement?.blur()")
                        await page.wait_for_timeout(150)
                    break
                if status != "RECORDING":
                    raise RuntimeError("Recording no longer active")
                await asyncio.sleep(1)
        finally:
            await browser.close()
    # Identical completion retries return the same draft.
    for attempt in range(3):
        try:
            result = await client.post(f"/api/agent-control/recordings/{request['id']}/complete", json=recording.data)
            result.raise_for_status()
            return result.json()
        except httpx.TransportError:
            if attempt == 2:
                recovery = CONFIG.parent / f"recording-{request['id']}.json"
                recovery.write_text(json.dumps(recording.data), encoding="utf-8")
                raise
            await asyncio.sleep(2)


async def serve(config, report, stopping):
    url = urlparse(config["server"])
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}):
        raise ValueError('Use HTTPS for a remote AssureDen server')
    async with httpx.AsyncClient(base_url=config["server"], timeout=30,
                                 headers={"X-Agent-Api-Key": config["key"]}) as client:
        if config.get('code'):
            response = await client.post('/api/agent-control/claim', json={
                'code': config['code'], 'key_hash': hashlib.sha256(config['key'].encode()).hexdigest()})
            response.raise_for_status()
            config.pop('code'); save_config(config)
        worker = AgentWorker(client, channel='msedge', headless=False)
        while not stopping.is_set():
            try:
                heartbeat = await client.post('/api/agents/heartbeat-extended', json=DESKTOP_CAPABILITIES)
                heartbeat.raise_for_status()
                result = await client.post('/api/agent-control/poll'); result.raise_for_status()
                recording = result.json()
                if recording:
                    report('Recording in Edge. Stop from AssureDen to review the draft.')
                    try:
                        await capture(client, recording)
                    except Exception:
                        await client.post(f"/api/agent-control/recordings/{recording['id']}/failed")
                        report('Recording failed. Check the target URL and retry from AssureDen.')
                else:
                    report('Connected. Ready for recording and test runs from AssureDen.')
                    # Preserve desktop capabilities; worker.poll_once advertises CLI capabilities.
                    response = await client.post('/api/agents/poll'); response.raise_for_status()
                    if 'execute_request' in response.json():
                        report('Executing a test. Follow progress in AssureDen.')
                        await worker.execute(response.json()['execute_request'])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    report('Agent access was removed. Generate a new pairing code in AssureDen and pair again.')
                    return
                report('Server rejected the request. Check AssureDen and retry.')
            except httpx.HTTPError:
                report('Disconnected or agent removed. Check AssureDen and your connection.')
            await asyncio.sleep(2)


def main():
    import tkinter as tk
    from tkinter import ttk
    if os.name != 'nt':
        raise SystemExit('This desktop installer supports Windows.')
    root = tk.Tk(); root.title('AssureDen Agent'); root.geometry('580x280')
    status = tk.StringVar(value='Pair this computer from AssureDen → Agents.')
    server = tk.StringVar(value='http://127.0.0.1:8000')
    code = tk.StringVar()
    messages = queue.Queue(); stopping = threading.Event()
    running = False
    ttk.Label(root, text='AssureDen Agent', font=('Segoe UI', 17)).pack(pady=12)
    ttk.Label(root, text='Server address').pack(); ttk.Entry(root, textvariable=server, width=65).pack()
    ttk.Label(root, text='One-time pairing code from AssureDen').pack(pady=(8,0))
    ttk.Entry(root, textvariable=code, width=65, show='•').pack()

    def connect(saved=None):
        nonlocal running
        if running:
            return
        config = saved or {'server': server.get().strip().rstrip('/'), 'code': code.get().strip(), 'key': secrets.token_urlsafe(32)}
        if not config.get('key') or (saved is None and not config['code']):
            status.set('Enter the pairing code shown in AssureDen.'); return
        try:
            save_config(config)  # Retain device identity if a claim response is lost.
        except OSError:
            status.set('Cannot save the connection securely. Check access to your local application data folder.')
            return
        running = True; button.config(state='disabled')
        def task():
            try:
                asyncio.run(serve(config, messages.put, stopping))
            except Exception:
                messages.put('Pairing failed. Check the server address and generate a new code if expired.')
            finally:
                messages.put(None)
        threading.Thread(target=task, daemon=True).start()

    button = ttk.Button(root, text='Pair and connect', command=connect); button.pack(pady=12)
    ttk.Label(root, textvariable=status, wraplength=550).pack()
    def update():
        nonlocal running
        while not messages.empty():
            message = messages.get()
            if message is None:
                running = False; button.config(state='normal')
            else:
                status.set(message)
        root.after(200, update)
    root.after(200, update)
    try:
        if CONFIG.exists():
            config = json.loads(protect(CONFIG.read_bytes(), decrypt=True)); server.set(config['server'])
            root.after(300, lambda: connect(config))
    except Exception:
        status.set('Saved connection unavailable. Pair this computer again.')
    root.mainloop()
    stopping.set()


if __name__ == '__main__':
    main()

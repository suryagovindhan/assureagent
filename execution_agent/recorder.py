"""Local, deterministic recorder. Review the exported file before promotion."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

CAPTURE_SCRIPT = r"""
(() => {
  if (window !== window.top) {
    window.__assuredenWarn('Frame interactions need manual authoring.').catch(() => {}); return;
  }
  if (window.__assuredenCapturing) return;
  window.__assuredenCapturing = true;
  function locators(el) {
    const result = [], seen = new Set();
    const tag = el.tagName.toLowerCase();
    const stable = value => value && !/^(ember\d+|:r[\w:]+:|react-select-\d+|mui-\d+)|[a-f0-9]{12,}|\d{6,}/i.test(value);
    const quote = value => '"' + CSS.escape(value) + '"';
    const xpathQuote = value => !value.includes("'") ? "'" + value + "'" : !value.includes('"') ? '"' + value + '"' : "concat(" + value.split("'").map(x => "'" + x + "'").join(',"\'",') + ')';
    const unique = (type, value) => {
      try {
        if (type === 'XPATH') {
          const found = document.evaluate(value, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
          return found.snapshotLength === 1 && found.snapshotItem(0) === el;
        }
        const found = document.querySelectorAll(value);
        return found.length === 1 && found[0] === el;
      } catch { return false; }
    };
    const add = (type, value, selector, note) => {
      const key = type + ':' + value;
      if (!value || value.length > 1000 || seen.has(key) || result.length >= 16 || !unique(type === 'XPATH' ? 'XPATH' : 'CSS_SELECTOR', selector || value)) return;
      seen.add(key); result.push({type, value, priority: result.length + 1, is_primary: result.length === 0,
        is_active: true, added_by: 'RECORDER', notes: note});
    };
    for (const attr of ['data-testid', 'data-test', 'data-cy']) {
      const value = el.getAttribute(attr);
      if (value) add(attr === 'data-testid' ? 'TEST_ID' : 'CSS_SELECTOR', attr === 'data-testid' ? value : tag + '[' + attr + '=' + quote(value) + ']', (attr === 'data-testid' ? '' : tag) + '[' + attr + '=' + quote(value) + ']', 'Testing label');
    }
    if (stable(el.id)) add('ID', el.id, '[id=' + quote(el.id) + ']', 'Element name');
    const accessible = el.getAttribute('aria-label');
    if (accessible) add('ARIA_LABEL', accessible, '[aria-label=' + quote(accessible) + ']', 'Accessible label');
    for (const attr of ['name', 'placeholder', 'title', 'alt']) {
      const value = el.getAttribute(attr);
      if (value) add('CSS_SELECTOR', tag + '[' + attr + '=' + quote(value) + ']', null, {name:'Control name', placeholder:'Field hint', title:'Help text', alt:'Image description'}[attr]);
    }
    for (const label of el.labels || []) {
      const text = (label.textContent || '').replace(/\s+/g, ' ').trim();
      if (text && label.htmlFor) add('XPATH', '//' + tag + '[@id=//label[normalize-space(.)=' + xpathQuote(text) + ']/@for]', null, 'Field label');
    }
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!el.matches('input,textarea,select') && text && text.length <= 160) {
      add('XPATH', '//' + tag + '[normalize-space(.)=' + xpathQuote(text) + ']', null, 'Visible text');
    }
    if (el.matches('input[type="submit"],input[type="button"]'))
      add('CSS_SELECTOR', 'input[type=' + quote(el.type) + '][value=' + quote(el.value) + ']', null, 'Button text');
    const href = el.getAttribute('href');
    if (tag === 'a' && href && !/[?#]/.test(href) && !/^(javascript|data):/i.test(href))
      add('CSS_SELECTOR', 'a[href=' + quote(href) + ']', null, 'Link destination');
    const classes = [...el.classList].filter(x => stable(x) && !/^(active|selected|focus|hover|disabled)$/i.test(x));
    if (classes.length) add('CSS_SELECTOR', tag + classes.slice(0, 3).map(x => '.' + CSS.escape(x)).join(''), null, 'Element appearance');
    // Scoped stable ancestor alternatives, avoiding generated IDs such as ember1049.
    let ancestor = el.parentElement;
    for (let depth = 0; ancestor && depth < 5; depth++, ancestor = ancestor.parentElement) {
      const anchor = stable(ancestor.id) ? '#' + CSS.escape(ancestor.id) : ancestor.getAttribute('data-testid') ? '[data-testid=' + quote(ancestor.getAttribute('data-testid')) + ']' : null;
      if (!anchor) continue;
      const suffix = accessible ? '[aria-label=' + quote(accessible) + ']' : el.getAttribute('name') ? '[name=' + quote(el.getAttribute('name')) + ']' : classes.length ? '.' + CSS.escape(classes[0]) : '';
      add('CSS_SELECTOR', anchor + ' ' + tag + suffix, null, 'Within a named area');
    }
    const path = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const nodeTag = node.tagName.toLowerCase();
      const peers = node.parentElement ? [...node.parentElement.children].filter(x => x.tagName === node.tagName) : [node];
      const index = peers.indexOf(node) + 1;
      if (stable(node.id)) { path.unshift('#' + CSS.escape(node.id)); break; }
      path.unshift(nodeTag + ':nth-of-type(' + index + ')');
    }
    add('CSS_SELECTOR', path.join(' > '), null, 'Page position (may change)');
    // Full document path is independent of generated IDs.
    const full = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const peers = node.parentElement ? [...node.parentElement.children].filter(x => x.tagName === node.tagName) : [node];
      full.unshift(node.tagName.toLowerCase() + '[' + (peers.indexOf(node) + 1) + ']');
    }
    add('XPATH', '/' + full.join('/'), null, 'Document position (may change)');
    return result;
  }
  const send = (el, action, value = '', secret = false) => {
    window.__assuredenRecord({action, input_value: value, is_secret: secret, locators: locators(el)}).catch(() => {});
  };
  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : event.target.parentElement;
    if (!target) return;
    const el = target.closest('button,a,input,select,textarea,[role="button"],[role="menuitem"],[role="tab"]') || target;
    if (el.matches('select,textarea,input:not([type="submit"]):not([type="button"])')) return;
    send(el, 'CLICK');
  }, true);
  document.addEventListener('change', event => {
    const el = event.target;
    if (el.matches('input[type="file"],input[type="radio"],select[multiple]')) {
      window.__assuredenWarn('File, radio and multi-select inputs need manual authoring.').catch(() => {}); return;
    }
    if (el.matches('input[type="checkbox"]')) return send(el, el.checked ? 'CHECK' : 'UNCHECK');
    if (el.matches('select:not([multiple])')) return send(el, 'SELECT', el.value);
    if (el.matches('input,textarea')) {
      const secret = el.type === 'password';
      send(el, 'TYPE', secret ? '' : el.value, secret);
    }
  }, true);
})();
"""


class RecordingSession:
    def __init__(self, name, url):
        self.data = {"schema_version": 1, "name": name,
                     "warnings": [],
                     "steps": [{"action": "NAVIGATE", "input_value": url, "locators": [], "is_secret": False}]}

    async def attach(self, context):
        primary = None
        def note_page(page):
            nonlocal primary
            if primary is None:
                primary = page
            else:
                warn(None, "New tabs and popups need manual authoring.")
        def warn(source, message):
            if message not in self.data["warnings"] and len(self.data["warnings"]) < 20:
                self.data["warnings"].append(message[:200])
        context.on("page", note_page)
        await context.expose_binding("__assuredenWarn", warn)
        def capture(source, event):
            if source["page"] != primary or source["frame"] != source["page"].main_frame:
                return
            if len(self.data["steps"]) >= 500:
                warn(None, "Recording reached 500 steps; later actions were not captured.")
                return
            if event.get("is_secret"):
                event["input_value"] = "{{RECORDED_SECRET_" + str(len(self.data["steps"])) + "}}"
            self.data["steps"].append(event)
        await context.expose_binding("__assuredenRecord", capture)
        await context.add_init_script(CAPTURE_SCRIPT)


async def main():
    parser = argparse.ArgumentParser(description="Record a browser session for AssureDen Drafts")
    parser.add_argument("url")
    parser.add_argument("--name", default="Recorded test")
    parser.add_argument("--output", default="recording.json")
    parser.add_argument("--channel", default=None)
    args = parser.parse_args()
    if urlparse(args.url).scheme not in {"http", "https"}:
        parser.error("Use an HTTP or HTTPS URL")
    if Path(args.output).exists():
        parser.error("Output already exists; choose a new recording filename")
    recording = RecordingSession(args.name, args.url)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=args.channel, headless=False)
            closed = asyncio.Event()
            browser.on("disconnected", lambda: closed.set())
            context = await browser.new_context()
            await recording.attach(context)
            page = await context.new_page()
            await page.goto(args.url)
            print("Recording. Finish inputs by leaving the field. Close the browser to save.", flush=True)
            await closed.wait()
    finally:
        Path(args.output).write_text(json.dumps(recording.data, indent=2), encoding="utf-8")
        print(f"Saved {len(recording.data['steps'])} steps to {args.output}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

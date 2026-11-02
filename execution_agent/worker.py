"""Deterministic browser execution over the canonical authenticated HTTP protocol."""
import asyncio
import json
import re
import time
from datetime import datetime, timezone

import httpx
from playwright.async_api import async_playwright, expect, Error as BrowserError

SUPPORTED_ACTIONS = [
    "NAVIGATE", "CLICK", "DOUBLE_CLICK", "RIGHT_CLICK", "TYPE", "APPEND",
    "CLEAR", "SELECT", "CHECK", "UNCHECK", "HOVER", "SCROLL_TO", "WAIT_FOR",
    "PRESS_KEY", "SCREENSHOT",
]
CAPABILITIES = {"protocol_version": 2, "version": "0.2.0", "supported_actions": SUPPORTED_ACTIONS}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class AgentWorker:
    def __init__(self, client: httpx.AsyncClient, *, channel=None, headless=True):
        self.client = client
        self.channel = channel
        self.headless = headless

    async def post(self, path, **kwargs):
        # Retry only identical requests. Server callbacks are idempotent.
        for attempt in range(3):
            try:
                response = await self.client.post(path, **kwargs)
                response.raise_for_status()
                return response
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))

    async def poll_once(self):
        await self.post("/api/agents/heartbeat-extended", json=CAPABILITIES)
        response = await self.post("/api/agents/poll")
        payload = response.json()
        if "execute_request" not in payload:
            return None
        request = payload["execute_request"]
        await self.execute(request)
        return request["run_id"]

    async def update(self, request, **body):
        await self.post(f"/api/runs/{request['run_id']}/agent-update",
                        json={"lease_id": request["lease_id"], **body})

    async def _renew(self, request):
        while True:
            await asyncio.sleep(10)
            await self.update(request, status="RUNNING")
            await self.post("/api/agents/heartbeat-extended", json=CAPABILITIES)

    async def execute(self, request):
        await self.update(request, status="RUNNING")
        heartbeat = asyncio.create_task(self._renew(request))
        execution = asyncio.create_task(self._execute_browser(request))
        try:
            done, _ = await asyncio.wait([heartbeat, execution], return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # Abort/lease rejection or lost connectivity fences browser execution.
                await heartbeat
            await execution
        finally:
            for task in (heartbeat, execution):
                task.cancel()
            await asyncio.gather(heartbeat, execution, return_exceptions=True)

    async def _execute_browser(self, request):
        failed = False
        try:
            if request["execution_plan"].get("version") != 2:
                raise ValueError("Unsupported execution plan")
            async with asyncio.timeout(request["metadata"]["timeout_seconds"]):
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(channel=self.channel, headless=self.headless)
                    try:
                        page = await browser.new_page()
                        for step in request["execution_plan"]["steps"]:
                            result, screenshot = await execute_step(page, step, skip=failed)
                            await self.update(request, step_results=[result])
                            if screenshot is not None:
                                # Do not retry uploads automatically; avoid duplicate evidence.
                                response = await self.client.post(
                                    f"/api/runs/{request['run_id']}/agent-artifacts",
                                    data={"lease_id": request["lease_id"],
                                          "execution_step_id": step["execution_step_id"]},
                                    files={"file": (f"step-{step['position']}.png", screenshot, "image/png")},
                                )
                                response.raise_for_status()
                            failed |= result["status"] == "FAILED"
                    finally:
                        await browser.close()
        except httpx.HTTPError:
            # Never overwrite an operator abort or continue after a rejected lease.
            raise
        except Exception:
            # Browser errors can include input/DOM secrets: do not send raw exceptions.
            await self.update(request, status="FAILED", error_message="Browser execution could not finish")
            return
        await self.update(request, status="FAILED" if failed else "COMPLETED")


def candidate_locator(scope, definition):
    strategy, value = definition["strategy"], definition["selector"]
    if strategy == "CSS_SELECTOR":
        return scope.locator(value)
    if strategy == "XPATH":
        return scope.locator("xpath=" + value)
    if strategy == "ID":
        return scope.locator("[id=" + json.dumps(value) + "]")
    if strategy == "ARIA_LABEL":
        return scope.locator("[aria-label=" + json.dumps(value) + "]")
    if strategy == "TEST_ID":
        return scope.get_by_test_id(value)
    if strategy == "TEXT":
        return scope.get_by_text(value, exact=True)
    raise ValueError("Unsupported locator strategy")


async def locate(scope, definition, timeout, *, allow_missing=False, allow_many=False):
    if not definition:
        raise ValueError("Action or assertion requires a locator")
    candidates = [definition, *definition.get("fallbacks", [])]
    # Poll all alternatives together so a stale first choice cannot consume
    # the entire time budget before a valid fallback is examined.
    deadline = time.monotonic() + max(.1, timeout / 1000 * .65)
    while True:
        for candidate in candidates:
            try:
                locator = candidate_locator(scope, candidate)
                count = await locator.count()
                if count == 1 or (allow_many and count > 0):
                    return locator
            except BrowserError:
                continue
        if allow_missing or time.monotonic() >= deadline:
            break
        await asyncio.sleep(.1)
    if allow_missing:
        return candidate_locator(scope, definition)
    raise ValueError("No unique active locator matched")


async def check_assertion(page, scope, assertion, timeout):
    kind = assertion["assertion_type"]
    value = assertion.get("expected_value", "")
    negative = assertion.get("is_negated", False)
    if kind in {"URL_EQUALS", "URL_CONTAINS", "TITLE_EQUALS"}:
        target = page
        method = "to_have_title" if kind == "TITLE_EQUALS" else "to_have_url"
        args = [re.compile(re.escape(value)) if kind == "URL_CONTAINS" else value]
    else:
        target = await locate(scope, assertion.get("locator"), timeout,
                              allow_missing=kind in {"NOT_VISIBLE", "ELEMENT_COUNT"} or (kind == "VISIBLE" and negative),
                              allow_many=kind == "ELEMENT_COUNT")
        rules = {
            "VISIBLE": ("to_be_visible", []), "NOT_VISIBLE": ("to_be_visible", []),
            "TEXT_EQUALS": ("to_have_text", [value]), "TEXT_CONTAINS": ("to_contain_text", [value]),
            "TEXT_MATCHES": ("to_have_text", [re.compile(value) if kind == "TEXT_MATCHES" else value]),
            "VALUE_EQUALS": ("to_have_value", [value]),
            "ATTRIBUTE_EQUALS": ("to_have_attribute", [assertion.get("attribute_name"), value]),
            "ELEMENT_COUNT": ("to_have_count", [int(value) if kind == "ELEMENT_COUNT" else 0]),
            "ENABLED": ("to_be_enabled", []), "DISABLED": ("to_be_enabled", []),
            "CHECKED": ("to_be_checked", []), "UNCHECKED": ("to_be_checked", []),
        }
        method, args = rules[kind]
        negative ^= kind in {"NOT_VISIBLE", "DISABLED", "UNCHECKED"}
    if negative:
        method = "not_" + method
    await getattr(expect(target), method)(*args, timeout=timeout)


async def execute_step(page, step, *, skip=False):
    start = time.monotonic()
    result = {k: step[k] for k in ("execution_step_id", "step_id", "step_version", "position", "action")}
    result.update(attempt=1, started_at=timestamp(), assertions=[])
    screenshot = None
    stage = "action"
    try:
        if skip:
            result["status"] = "SKIPPED"
        else:
            timeout = step["timeout_ms"]
            async with asyncio.timeout(timeout / 1000):
                page.set_default_timeout(timeout)
                hints, metadata = step.get("execution_hint", {}), step.get("step_metadata", {})
                if set(hints) - {"iframe_selector", "focus_before_action"}:
                    raise ValueError("Unsupported execution hint")
                if any(v for k, v in metadata.items() if k != "execution"):
                    raise ValueError("Unsupported execution metadata")
                options = metadata.get("execution", {})
                if set(options) - {"clear_before_type", "press_enter"}:
                    raise ValueError("Unsupported execution options")
                scope = page.frame_locator(hints["iframe_selector"]) if hints.get("iframe_selector") else page
                action, value = step["action"], step.get("resolved_input", "")
                if action not in SUPPORTED_ACTIONS:
                    raise ValueError("Unsupported action")
                if action == "NAVIGATE":
                    await page.goto(value, wait_until="domcontentloaded")
                elif action == "SCREENSHOT":
                    screenshot = await page.screenshot()
                else:
                    stage = "finding"
                    target = await locate(scope, step.get("locator"), timeout)
                    stage = "action"
                    if hints.get("focus_before_action"):
                        await target.focus()
                    if action == "TYPE":
                        if options.get("clear_before_type", True):
                            await target.fill(value)
                        else:
                            await target.press_sequentially(value)
                    elif action == "APPEND":
                        await target.press("End")
                        await target.press_sequentially(value)
                    elif action == "CLEAR":
                        await target.fill("")
                    elif action == "SELECT":
                        await target.select_option(value)
                    elif action == "PRESS_KEY":
                        await target.press(value)
                    elif action == "RIGHT_CLICK":
                        await target.click(button="right")
                    else:
                        method = {"CLICK": "click", "DOUBLE_CLICK": "dblclick", "CHECK": "check",
                                  "UNCHECK": "uncheck", "HOVER": "hover", "SCROLL_TO": "scroll_into_view_if_needed",
                                  "WAIT_FOR": "wait_for"}[action]
                        await getattr(target, method)()
                    if options.get("press_enter"):
                        await target.press("Enter")
                stage = "check"
                fatal_failed = False
                assertions = step.get("assertions", [])
                for assertion in assertions:
                    passed = True
                    try:
                        remaining = max(1, timeout - int((time.monotonic() - start) * 1000))
                        await check_assertion(page, scope, assertion, max(1, remaining // (len(assertions) + 1)))
                    except (AssertionError, BrowserError, ValueError, KeyError, re.error):
                        passed = False
                    result["assertions"].append({"assertion_id": assertion["assertion_id"],
                                                  "status": "PASSED" if passed else "FAILED"})
                    fatal_failed |= not passed and assertion.get("is_fatal", True)
                if fatal_failed:
                    raise ValueError("Required assertion failed")
                result["status"] = "PASSED"
    except Exception:
        result["status"] = "OPTIONAL_FAILED" if step.get("is_optional") else "FAILED"
        result["error_message"] = {"finding": "Could not find one matching element. Review How to Find It for this step.", "action": "The element could not be used or the page could not open. It may be hidden, covered, disabled, or still loading.", "check": "The expected result did not match the page."}[stage]
        if step.get("screenshot_on_failure", True):
            try:
                screenshot = await page.screenshot(timeout=2000)
            except BrowserError:
                pass
    result.update(completed_at=timestamp(), duration_ms=int((time.monotonic() - start) * 1000))
    return result, screenshot

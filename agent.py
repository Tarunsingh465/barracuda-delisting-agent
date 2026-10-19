"""Barracuda reputation lookup and carefully guarded removal-request agent.

The agent stops on HTTP 403/429, security challenges, or uncertain lookup results.
It never retries an ambiguous removal submission automatically.
"""

from __future__ import annotations

import csv
import ipaddress
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

LOOKUP_URL = os.getenv(
    "BARRACUDA_LOOKUP_URL",
    "https://barracudacentral.org/lookups/lookup-reputation",
).strip()
FORM_URL = os.getenv(
    "BARRACUDA_FORM_URL",
    "https://www.barracudacentral.org/rbl/removal-request",
).strip()

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "").strip()
CUSTOMER_NAME = os.getenv("CUSTOMER_NAME", "").strip()
CUSTOMER_EMAIL = os.getenv("CUSTOMER_EMAIL", "").strip()
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "").strip()
REMOVAL_REASON = os.getenv("REMOVAL_REASON", "").strip()

INPUT_PATH = os.getenv("INPUT_IPS_PATH", "input_ips.csv")
RESULTS_PATH = os.getenv("RESULTS_PATH", "results.csv")
DB_PATH = os.getenv("DB_PATH", "agent.db")
ERROR_LOG_PATH = os.getenv("ERROR_LOG_PATH", "agent_errors.log")

BATCH_SIZE = 10
REQUEST_PAUSE_SECONDS = 2.0
TYPE_DELAY_MIN_MS = 40
TYPE_DELAY_MAX_MS = 120
TIMEOUT_MS = 30_000
LOOKUP_RESULT_TIMEOUT_MS = 12_000
LOOKUP_POLL_INTERVAL_MS = 250
CONFIRMATION_TIMEOUT_MS = 180_000  # 3 minutes
CONFIRMATION_POLL_MS = 1_000
ACCESS_RESTRICTION_STATUSES = {403, 429}
SUBMISSION_STATUSES_TO_SKIP = {
    "SUBMISSION_ATTEMPTED",
    "SUBMISSION_CONFIRMED",
    "SUBMISSION_UNCONFIRMED",
    "SUBMITTED",  # Backward compatibility with earlier database versions.
}


def configure_error_logging() -> logging.Logger:
    """Create one UTF-8 diagnostic logger, with timestamps explicitly in UTC."""
    logger = logging.getLogger("barracuda_agent")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = str(Path(ERROR_LOG_PATH).resolve())
    already_configured = any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", None) == target
        for handler in logger.handlers
    )
    if not already_configured:
        handler = logging.FileHandler(target, mode="a", encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s UTC | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


LOGGER = configure_error_logging()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_sensitive_text(text: object) -> str:
    """Redact configured contact details and ordinary email addresses."""
    result = str(text or "")
    for secret in (CONTACT_EMAIL, CUSTOMER_EMAIL, PHONE_NUMBER, CUSTOMER_NAME):
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        result,
        flags=re.IGNORECASE,
    )
    return result[:4_000]


def log_agent_error(stage: str, ip: str, message: str, exc: Exception | None = None) -> None:
    safe_message = redact_sensitive_text(message)
    if exc is not None:
        LOGGER.error(
            "AGENT_ERROR | stage=%s | ip=%s | message=%s",
            stage,
            ip,
            safe_message,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        LOGGER.error(
            "AGENT_ERROR | stage=%s | ip=%s | message=%s",
            stage,
            ip,
            safe_message,
        )


def is_barracuda_url(url: str) -> bool:
    """Accept only the official domain and its subdomains for status tracking."""
    try:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return hostname == "barracudacentral.org" or hostname.endswith(".barracudacentral.org")


def restriction_detail(status: int, status_text: str, url: str, stage: str) -> str:
    readable_status = f"HTTP {status} {status_text}".strip()
    return (
        f"{stage} received {readable_status} from {url}. "
        "Processing stopped; do not retry automatically or attempt to bypass the restriction."
    )


def attach_page_diagnostics(page, state: dict[str, object]) -> None:
    """Log browser failures and record official-site 403/429 responses for control flow."""

    def on_response(response) -> None:
        status = response.status
        request = response.request
        resource_type = request.resource_type

        if status in ACCESS_RESTRICTION_STATUSES and is_barracuda_url(response.url):
            # Include documents, fetch/XHR, and first-party assets: a restricted
            # first-party response means we should stop rather than guess.
            state["restriction"] = {
                "status": status,
                "status_text": response.status_text,
                "url": response.url,
                "stage": state.get("stage", "UNKNOWN"),
            }

        if status < 400:
            return

        try:
            body = response.text()
        except Exception as exc:  # Some responses no longer have readable bodies.
            body = f"[Response body unavailable: {type(exc).__name__}: {exc}]"

        try:
            headers = response.headers
            useful_headers = {
                key: headers[key]
                for key in ("content-type", "server", "cf-ray", "x-request-id", "request-id")
                if key in headers
            }
        except Exception:
            useful_headers = {}

        LOGGER.error(
            "HTTP_RESPONSE_ERROR | stage=%s | ip=%s | status=%s | "
            "status_text=%s | resource_type=%s | url=%s | headers=%s | body=%s",
            state.get("stage", "UNKNOWN"),
            state.get("ip", "UNKNOWN"),
            status,
            response.status_text,
            resource_type,
            response.url,
            useful_headers,
            redact_sensitive_text(body),
        )

    def on_request_failed(request) -> None:
        LOGGER.error(
            "REQUEST_FAILED | stage=%s | ip=%s | method=%s | "
            "resource_type=%s | url=%s | failure=%s",
            state.get("stage", "UNKNOWN"),
            state.get("ip", "UNKNOWN"),
            request.method,
            request.resource_type,
            request.url,
            request.failure or "No failure reason supplied by browser",
        )

    def on_page_error(error) -> None:
        LOGGER.error(
            "BROWSER_PAGE_ERROR | stage=%s | ip=%s | error=%s",
            state.get("stage", "UNKNOWN"),
            state.get("ip", "UNKNOWN"),
            redact_sensitive_text(error),
        )

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    page.on("pageerror", on_page_error)


def take_restriction(state: dict[str, object] | None, stage: str) -> tuple[str, str] | None:
    """Convert a captured 403/429 into a STOP_REQUIRED result."""
    if not state:
        return None
    restriction = state.get("restriction")
    if not isinstance(restriction, dict):
        return None
    state["restriction"] = None
    status = int(restriction.get("status", 0))
    detail = restriction_detail(
        status,
        str(restriction.get("status_text", "")),
        str(restriction.get("url", "unknown URL")),
        stage,
    )
    return "STOP_REQUIRED", detail


def clear_restriction(state: dict[str, object] | None) -> None:
    if state is not None:
        state["restriction"] = None


def type_text(locator, value: str) -> None:
    """Type sequentially using one randomly selected delay per field."""
    locator.click(timeout=TIMEOUT_MS)
    locator.press("Control+A", timeout=TIMEOUT_MS)
    delay_ms = random.randint(TYPE_DELAY_MIN_MS, TYPE_DELAY_MAX_MS)
    print(f"Typing field with configured delay: {delay_ms} ms")
    locator.press_sequentially(str(value), delay=delay_ms, timeout=TIMEOUT_MS)


def security_challenge_detected(page) -> bool:
    """Detect visible challenge/access-denied wording without attempting a bypass."""
    try:
        text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=3_000)).lower()
    except Exception:
        return False
    markers = (
        "403 forbidden",
        "429 too many requests",
        "forbidden",
        "access denied",
        "request blocked",
        "verify you are human",
        "are you human",
        "captcha",
        "too many requests",
        "rate limit",
        "unusual traffic",
        "security challenge",
        "temporarily blocked",
    )
    return any(marker in text for marker in markers)


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ip_jobs (
                    ip TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    detail TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )


def get_status(ip: str) -> str | None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT status FROM ip_jobs WHERE ip = ?", (ip,)).fetchone()
    return row[0] if row else None


def should_skip_submission(status: str | None, detail: str | None = None) -> bool:
    """Prevent automatic retries for any submission attempt whose outcome is uncertain."""
    if status in SUBMISSION_STATUSES_TO_SKIP:
        return True
    # A form POST may have been attempted before a 403/429 was seen. Do not resubmit
    # that IP automatically even though the final status is STOP_REQUIRED.
    return status == "STOP_REQUIRED" and bool(
        detail and "submission was attempted" in detail.lower()
    )


def record(ip: str, status: str, detail: str = "") -> None:
    timestamp = now()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        with conn:
            conn.execute(
                """INSERT INTO ip_jobs(ip, status, detail, updated_at)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(ip) DO UPDATE SET
                       status = excluded.status,
                       detail = excluded.detail,
                       updated_at = excluded.updated_at""",
                (ip, status, detail, timestamp),
            )

    results_file = Path(RESULTS_PATH)
    needs_header = not results_file.exists() or results_file.stat().st_size == 0
    with results_file.open("a", newline="", encoding="utf-8") as file_handle:
        writer = csv.writer(file_handle)
        if needs_header:
            writer.writerow(["timestamp_utc", "ip", "status", "detail"])
        writer.writerow([timestamp, ip, status, detail])


def load_ips() -> list[str]:
    """Load, validate, normalize, and de-duplicate IPv4 addresses in input order."""
    input_file = Path(INPUT_PATH)
    if not input_file.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_PATH}")

    ips: list[str] = []
    seen: set[str] = set()
    with input_file.open(newline="", encoding="utf-8-sig") as file_handle:
        reader = csv.DictReader(file_handle)
        if not reader.fieldnames or "ip" not in {name.strip().lower() for name in reader.fieldnames}:
            raise ValueError("input_ips.csv must contain a column named 'ip'.")

        # Support 'IP' as well as 'ip' without silently accepting another column.
        ip_column = next(name for name in reader.fieldnames if name.strip().lower() == "ip")
        for row in reader:
            raw = (row.get(ip_column) or "").strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                parsed = ipaddress.ip_address(raw)
            except ValueError:
                record(raw, "INVALID_IP", "Input is not a valid IP address")
                print(f"Skipping invalid IP entry: {raw!r}")
                continue
            if parsed.version != 4:
                record(str(parsed), "UNSUPPORTED_IP_VERSION", "Only IPv4 is supported")
                print(f"Skipping unsupported IPv6 entry: {raw!r}")
                continue
            normalized = str(parsed)
            if normalized not in seen:
                seen.add(normalized)
                ips.append(normalized)
    return ips


def fill_lookup_ip(page, ip: str) -> bool:
    """Fill the lookup field and verify that the intended IP is present."""
    candidates = []
    try:
        labelled = page.get_by_label("IP or Domain", exact=False)
        if labelled.count():
            candidates.append(labelled.first)
    except Exception:
        pass

    try:
        visible_inputs = page.locator("input:visible")
        for index in range(visible_inputs.count()):
            candidate = visible_inputs.nth(index)
            if candidate not in candidates:
                try:
                    input_type = (candidate.get_attribute("type") or "text").lower()
                    if input_type in {"text", "search", "url"}:
                        candidates.append(candidate)
                except Exception:
                    continue
    except Exception:
        pass

    for field in candidates:
        try:
            type_text(field, ip)
            if field.input_value(timeout=3_000).strip() == ip:
                return True
        except Exception:
            continue
    return False


def click_lookup(page) -> bool:
    """Click one clear lookup control; do not fall back after a failed click."""
    try:
        button = page.get_by_role("button", name=re.compile(r"check\s+reputation", re.I))
        if button.count() == 1:
            button.click(timeout=TIMEOUT_MS, no_wait_after=True)
            return True
    except Exception:
        return False

    # Fallback only when there is no unique button; only click a unique visible match.
    try:
        matches = page.get_by_text("Check Reputation", exact=True)
        if matches.count() == 1 and matches.is_visible():
            matches.click(timeout=TIMEOUT_MS, no_wait_after=True)
            return True
    except Exception:
        return False
    return False


def classify_lookup_text(ip: str, body_text: str) -> tuple[str, str]:
    """Classify only explicit result text tied to the IP being queried."""
    text = re.sub(r"\s+", " ", body_text or "").lower()
    ip_re = rf"(?<![\d.]){re.escape(ip.lower())}(?![\d.])"

    not_listed_patterns = (
        rf"{ip_re}.{{0,300}}not currently listed as\s+['\"]?poor",
        rf"{ip_re}.{{0,300}}not listed as\s+['\"]?poor",
    )
    if any(re.search(pattern, text) for pattern in not_listed_patterns):
        return "NOT_LISTED", "Official lookup text says this IP is not currently listed as poor."

    listed_patterns = (
        rf"{ip_re}.{{0,300}}(?:is currently listed as|is listed as)\s+['\"]?poor",
    )
    if any(re.search(pattern, text) for pattern in listed_patterns):
        return "LISTED", "Official lookup text says this IP is listed as poor."

    return "CHECK_ERROR", "Could not verify an explicit reputation result for this exact IP."


def classify_page(page, ip: str) -> tuple[str, str]:
    if security_challenge_detected(page):
        return "STOP_REQUIRED", "Security challenge or access-control page detected; stopping without bypassing it."
    return classify_lookup_text(ip, page.locator("body").inner_text(timeout=5_000))


def lookup_form_is_ready(page) -> bool:
    """Check whether the existing page can accept another IP without navigation."""
    try:
        current = urlparse(page.url)
        expected = urlparse(LOOKUP_URL)
        if current.hostname != expected.hostname or current.path.rstrip("/") != expected.path.rstrip("/"):
            return False

        button = page.get_by_role("button", name=re.compile(r"check\s+reputation", re.I))
        control_ready = button.count() == 1 and button.is_visible()
        if not control_ready:
            text_control = page.get_by_text("Check Reputation", exact=True)
            control_ready = text_control.count() == 1 and text_control.is_visible()
        if not control_ready:
            return False

        inputs = page.locator("input:visible")
        for index in range(inputs.count()):
            field = inputs.nth(index)
            input_type = (field.get_attribute("type") or "text").lower()
            if input_type in {"text", "search", "url"} and field.is_visible():
                return True
        return False
    except Exception:
        return False


def website_lookup(page, ip: str, diagnostic_state: dict[str, object] | None = None) -> tuple[str, str]:
    """Query one IP on the persistent lookup page and verify its exact result.

    Once the official lookup form is already visible, keep using it instead of
    performing another top-level navigation for every IP. Any HTTP 403/429 or
    visible challenge still stops the run; this behavior does not bypass limits.
    """
    clear_restriction(diagnostic_state)
    if not lookup_form_is_ready(page):
        try:
            response = page.goto(LOOKUP_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        except Exception:
            restriction = take_restriction(diagnostic_state, "Lookup navigation")
            if restriction:
                return restriction
            raise

        if response and response.status in ACCESS_RESTRICTION_STATUSES:
            return "STOP_REQUIRED", restriction_detail(
                response.status, response.status_text, response.url, "Lookup navigation"
            )
        restriction = take_restriction(diagnostic_state, "Lookup page load")
        if restriction:
            return restriction

    if security_challenge_detected(page):
        return "STOP_REQUIRED", "Security challenge or access-control page detected; stopping without bypassing it."

    if not fill_lookup_ip(page, ip):
        return "CHECK_ERROR", "Could not locate, fill, and verify the lookup IP field."

    # Check server restrictions caused by the lookup-button request as well.
    clear_restriction(diagnostic_state)
    try:
        clicked = click_lookup(page)
    except Exception:
        restriction = take_restriction(diagnostic_state, "Lookup submission")
        if restriction:
            return restriction
        raise

    restriction = take_restriction(diagnostic_state, "Lookup submission")
    if restriction:
        return restriction
    if not clicked:
        return "CHECK_ERROR", "Could not uniquely identify and click the Check Reputation control."

    deadline = time.monotonic() + LOOKUP_RESULT_TIMEOUT_MS / 1_000
    last_detail = "Waiting for the official lookup result."
    while time.monotonic() < deadline:
        restriction = take_restriction(diagnostic_state, "Lookup result")
        if restriction:
            return restriction
        if security_challenge_detected(page):
            return "STOP_REQUIRED", "Security challenge or access-control page detected after lookup; stopping."

        try:
            status, detail = classify_lookup_text(
                ip,
                page.locator("body").inner_text(timeout=2_000),
            )
            if status in {"LISTED", "NOT_LISTED"}:
                return status, detail
            last_detail = detail
        except PlaywrightTimeoutError:
            last_detail = "Lookup page text was not readable before the result timeout."

        page.wait_for_timeout(LOOKUP_POLL_INTERVAL_MS)

    restriction = take_restriction(diagnostic_state, "Lookup result")
    if restriction:
        return restriction
    return "CHECK_ERROR", (
        f"No verified result for {ip} appeared within {LOOKUP_RESULT_TIMEOUT_MS // 1000} seconds. "
        f"Last observation: {last_detail}"
    )


def fill_removal_form(page, ip: str, diagnostic_state: dict[str, object] | None = None) -> tuple[str, str]:
    """Fill and submit only after validating all required fields; never auto-retry."""
    missing = [
        name
        for name, value in {
            "CONTACT_EMAIL": CONTACT_EMAIL,
            "CUSTOMER_NAME": CUSTOMER_NAME,
            "CUSTOMER_EMAIL": CUSTOMER_EMAIL,
            "PHONE_NUMBER": PHONE_NUMBER,
            "REMOVAL_REASON": REMOVAL_REASON,
        }.items()
        if not value
    ]
    if missing:
        return "CONFIG_ERROR", "Missing .env values: " + ", ".join(missing)

    submission_attempted = False
    try:
        clear_restriction(diagnostic_state)
        response = page.goto(FORM_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        if response and response.status in ACCESS_RESTRICTION_STATUSES:
            return "STOP_REQUIRED", restriction_detail(
                response.status, response.status_text, response.url, "Removal form navigation"
            )
        restriction = take_restriction(diagnostic_state, "Removal form load")
        if restriction:
            return restriction
        if security_challenge_detected(page):
            return "STOP_REQUIRED", "Security challenge or access-control page detected on removal form; stopping."

        # The site's form layout can change; use this ordered fallback only after
        # checking the input count and verifying the first field contains the target IP.
        field_values = [
            ("IP address", ip),
            ("contact email", CONTACT_EMAIL),
            ("customer name", CUSTOMER_NAME),
            ("customer email", CUSTOMER_EMAIL),
            ("phone number", PHONE_NUMBER),
        ]
        inputs = page.locator("input:visible")
        text_inputs = []
        for index in range(inputs.count()):
            item = inputs.nth(index)
            input_type = (item.get_attribute("type") or "text").lower()
            if input_type in {"text", "email", "tel", "number", "search"}:
                text_inputs.append(item)
        if len(text_inputs) < len(field_values):
            return "FORM_FILL_ERROR", f"Expected at least 5 visible text inputs; found {len(text_inputs)}."

        for locator, (_, value) in zip(text_inputs[:5], field_values):
            type_text(locator, value)
        textareas = page.locator("textarea:visible")
        if textareas.count() != 1:
            return "FORM_FILL_ERROR", f"Expected one visible reason textarea; found {textareas.count()} ."
        type_text(textareas.first, REMOVAL_REASON)

        # Verify each field after typing before any external side effect.
        actual_values = [locator.input_value(timeout=3_000).strip() for locator in text_inputs[:5]]
        expected_values = [value for _, value in field_values]
        if actual_values != expected_values:
            return "FORM_FILL_ERROR", "Form field verification failed; submission was not attempted."
        if textareas.first.input_value(timeout=3_000).strip() != REMOVAL_REASON:
            return "FORM_FILL_ERROR", "Removal reason verification failed; submission was not attempted."

        form = text_inputs[0].locator("xpath=ancestor::form")
        submit_button = form.get_by_role("button", name=re.compile(r"submit|send request", re.I))
        if submit_button.count() != 1:
            return "SUBMISSION_NOT_ATTEMPTED", "Could not uniquely identify the form's Submit button."

        record(ip, "SUBMISSION_ATTEMPTED", "Form verified; a single submission attempt is about to start.")
        clear_restriction(diagnostic_state)
        submission_attempted = True
        submit_button.click(timeout=TIMEOUT_MS, no_wait_after=True)

        confirmation_phrases = (
            "request received",
            "request has been submitted",
            "request was submitted",
            "successfully submitted",
            "submission successful",
        )
        deadline = time.monotonic() + CONFIRMATION_TIMEOUT_MS / 1_000
        while time.monotonic() < deadline:
            restriction = take_restriction(diagnostic_state, "Removal submission")
            if restriction:
                return "STOP_REQUIRED", (
                    "A removal submission was attempted before the server returned a restriction. "
                    + restriction[1]
                    + " Manually verify the request before any further action."
                )
            try:
                text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=3_000)).strip()
                lowered = text.lower()
                if any(phrase in lowered for phrase in confirmation_phrases):
                    match = re.search(r"confirmation number is\s+([A-Z0-9-]+)", text, re.I)
                    if match:
                        return "SUBMISSION_CONFIRMED", f"Barracuda displayed a receipt confirmation. Reference: {match.group(1)}"
                    return "SUBMISSION_CONFIRMED", "Barracuda displayed a request-received confirmation; no reference number was extracted."
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(CONFIRMATION_POLL_MS)

        return "SUBMISSION_UNCONFIRMED", (
            "No recognized confirmation appeared within 3 minutes. Verify manually before retrying."
        )

    except Exception as exc:
        restriction = take_restriction(diagnostic_state, "Removal form processing")
        if restriction:
            if submission_attempted:
                return "STOP_REQUIRED", (
                    "A removal submission was attempted before the server returned a restriction. "
                    + restriction[1]
                    + " Manually verify the request before any further action."
                )
            return restriction
        if submission_attempted:
            return "SUBMISSION_UNCONFIRMED", (
                f"Submission was attempted but its outcome is unknown ({type(exc).__name__}: {exc}). "
                "Verify manually before retrying."
            )
        if isinstance(exc, PlaywrightTimeoutError):
            return "FORM_FILL_ERROR", f"Form operation timed out before submission: {exc}"
        return "FORM_FILL_ERROR", f"{type(exc).__name__}: {exc}; submission was not attempted."


def process_ips(context, lookup_page, ips: list[str], diagnostic_state: dict[str, object]) -> None:
    """Look up every input IP in one pass; batch only consecutive LISTED request-page work.

    The lookup page stays separate and remains open across all inputs. A request
    page is created only when an IP is explicitly LISTED. After 10 consecutive
    LISTED results have completed with confirmed receipt, only that request page
    is closed; the next listed IP starts the next request-page batch. A NOT_LISTED
    result resets the consecutive counter. Any restriction or uncertain result
    stops the run; batching must never be used to bypass a site restriction.
    """
    total = len(ips)
    request_page = None
    consecutive_listed = 0
    request_batch_number = 1

    for overall_index, ip in enumerate(ips, start=1):
        print(f"\n[{overall_index}/{total}] Checking {ip}")
        previous_status = get_status(ip)
        previous_detail = ""
        if previous_status == "STOP_REQUIRED":
            with closing(sqlite3.connect(DB_PATH)) as conn:
                row = conn.execute("SELECT detail FROM ip_jobs WHERE ip = ?", (ip,)).fetchone()
                previous_detail = row[0] if row else ""
        if should_skip_submission(previous_status, previous_detail):
            print("Skipping IP with a previous submission attempt; verify its status manually first.")
            consecutive_listed = 0
            continue

        diagnostic_state["stage"] = "LOOKUP"
        diagnostic_state["ip"] = ip
        try:
            status, detail = website_lookup(lookup_page, ip, diagnostic_state)
        except Exception as exc:
            restriction = take_restriction(diagnostic_state, "Lookup exception")
            if restriction:
                status, detail = restriction
            elif isinstance(exc, PlaywrightTimeoutError):
                status, detail = "CHECK_ERROR", f"Lookup timed out: {exc}"
            else:
                status, detail = "CHECK_ERROR", f"{type(exc).__name__}: {exc}"
            log_agent_error("LOOKUP", ip, detail, exc)

        print(f"Website result: {status} — {detail}")
        if status not in {"NOT_LISTED", "LISTED"}:
            log_agent_error("LOOKUP", ip, f"{status}: {detail}")
        record(ip, status, detail)

        if status == "STOP_REQUIRED":
            print("Safety stop triggered. No further IPs will be processed.")
            return
        if status == "CHECK_ERROR":
            print("Lookup was uncertain. Stopping to avoid acting on an unreliable page state.")
            return
        if status == "NOT_LISTED":
            consecutive_listed = 0
            print("Unlisted: no removal form will open.")
        elif status == "LISTED":
            consecutive_listed += 1
            print(f"Consecutive poor-listed IPs: {consecutive_listed}/{BATCH_SIZE} (request-page batch {request_batch_number})")
            if request_page is None or request_page.is_closed():
                request_page = context.new_page()
                attach_page_diagnostics(request_page, diagnostic_state)

            diagnostic_state["stage"] = "REMOVAL_FORM"
            diagnostic_state["ip"] = ip
            form_status, form_detail = fill_removal_form(request_page, ip, diagnostic_state)
            record(ip, form_status, form_detail)
            print(f"Removal form: {form_status} — {form_detail}")
            if form_status != "SUBMISSION_CONFIRMED":
                log_agent_error("REMOVAL_FORM", ip, f"{form_status}: {form_detail}")
            if form_status in {"STOP_REQUIRED", "FORM_FILL_ERROR", "CONFIG_ERROR", "SUBMISSION_NOT_ATTEMPTED"}:
                print("Form processing was not safely completed. Stopping the run.")
                return
            if form_status == "SUBMISSION_UNCONFIRMED":
                print("Submission is unconfirmed. Do not retry automatically; verify manually.")
                return

            if consecutive_listed == BATCH_SIZE:
                print(f"Request-page batch {request_batch_number} completed ({BATCH_SIZE} consecutive poor-listed IPs). Rotating only the request page; lookup page remains open.")
                request_page.close()
                request_page = None
                request_batch_number += 1
                consecutive_listed = 0

        time.sleep(REQUEST_PAUSE_SECONDS)

    if request_page is not None and not request_page.is_closed():
        request_page.close()
    print("All input IPs have been processed in one lookup run.")


def main() -> None:
    LOGGER.info("AGENT_START | input=%s", INPUT_PATH)
    init_db()
    try:
        ips = load_ips()
    except (OSError, csv.Error, ValueError) as exc:
        print(f"Input error: {exc}")
        log_agent_error("INPUT", "UNKNOWN", f"Input error: {exc}", exc)
        return

    if not ips:
        print("No valid IPv4 addresses found in the input file.")
        return

    print(f"Loaded {len(ips)} unique IPv4 address(es).")
    print(f"Lookup mode: all {len(ips)} IP(s) in one run; no lookup batches.")
    print(f"Request-page batch size: {BATCH_SIZE} consecutive poor-listed IPs.")
    print("The agent stops on HTTP 403/429, access challenges, or uncertain lookup results.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            try:
                lookup_page = context.new_page()
                diagnostic_state: dict[str, object] = {
                    "stage": "BROWSER_STARTUP",
                    "ip": "UNKNOWN",
                    "restriction": None,
                }
                attach_page_diagnostics(lookup_page, diagnostic_state)
                process_ips(context, lookup_page, ips, diagnostic_state)
            finally:
                context.close()
        finally:
            browser.close()

    LOGGER.info("AGENT_FINISHED | results=%s | database=%s", RESULTS_PATH, DB_PATH)
    print(f"\nFinished. Results: {RESULTS_PATH}; database: {DB_PATH}; errors: {ERROR_LOG_PATH}")


if __name__ == "__main__":
    main()

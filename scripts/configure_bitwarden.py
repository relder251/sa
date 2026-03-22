#!/usr/bin/env python3
"""
Configure Bitwarden browser extension to use the self-hosted Vaultwarden server.

Supported browsers: Google Chrome, Chromium, Firefox ESR

Approach:
- Chrome/Chromium: Playwright persistent context with extension loading
- Firefox: Selenium + geckodriver (installs XPI as temporary extension, then configures it)

Usage:
    /home/user/pw_automation/venv/bin/python3 configure_bitwarden.py
"""
import os
import re
import sys
import time
import shutil
from pathlib import Path

SERVER_URL = "https://vault.private.sovereignadvisory.ai"
EMAIL = "relder@sovereignadvisory.ai"
PASSWORD = "!#@$Purse0nal)(*&!"

CHROME_EXT_ID = "nngceckbapebfimnlniiiahkandclblb"
CHROME_EXT_PATH = str(
    Path.home()
    / ".config/google-chrome/Default/Extensions"
    / CHROME_EXT_ID
    / "2026.2.0_0"
)
CHROMIUM_EXT_PATH = str(
    Path.home()
    / ".config/chromium/Default/Extensions"
    / CHROME_EXT_ID
    / "2026.2.0_0"
)
FIREFOX_XPI = str(
    Path.home()
    / ".mozilla/firefox/eo3muy37.default-esr/extensions"
    / "{446900e4-71c2-419f-a6a7-df9c091e268b}.xpi"
)
GECKODRIVER = "/usr/bin/geckodriver"

SCREENSHOTS = Path("/tmp/bw_screenshots")
SCREENSHOTS.mkdir(exist_ok=True)


def ss(obj, name):
    """Take a screenshot via Playwright page or Selenium driver."""
    path = str(SCREENSHOTS / f"{name}.png")
    try:
        if hasattr(obj, "screenshot"):
            obj.screenshot(path=path)  # Playwright
        else:
            obj.save_screenshot(path)  # Selenium
        print(f"  [screenshot] {name}.png")
    except Exception as e:
        print(f"  [screenshot failed] {name}: {e}")


def configure_chromium_based(browser_name: str, ext_path: str) -> bool:
    """Configure Bitwarden in Chrome or Chromium using Playwright."""
    from playwright.sync_api import sync_playwright

    print(f"\n{'='*60}")
    print(f"Configuring {browser_name}")
    print(f"{'='*60}")

    if not Path(ext_path).exists():
        print(f"  ERROR: Extension not found at {ext_path}")
        return False

    tmp_profile = f"/tmp/bw-{browser_name.lower().replace(' ', '-')}-profile"

    def find_click(page, text):
        items = page.query_selector_all("button, li, [role='option']")
        for item in items:
            try:
                if item.is_visible() and text.lower() in item.inner_text().strip().lower():
                    item.click()
                    return True
            except Exception:
                pass
        return False

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=tmp_profile,
            headless=False,
            args=[
                f"--disable-extensions-except={ext_path}",
                f"--load-extension={ext_path}",
                "--no-first-run",
            ],
        )
        popup_url = f"chrome-extension://{CHROME_EXT_ID}/popup/index.html"
        page = context.new_page()
        page.goto(popup_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        ss(page, f"{browser_name.lower().replace(' ', '_')}_01_initial")

        # Click "Log in" if on intro/home page
        text = page.evaluate("() => document.body.innerText")
        if "Security, prioritized" in text or ("Create account" in text and "Log in" in text):
            page.locator("button:has-text('Log in')").first.click()
            time.sleep(2)

        # Click env selector (bitwarden.com button) -> self-hosted
        env_btns = page.query_selector_all("button")
        for btn in env_btns:
            try:
                if btn.is_visible() and "bitwarden.com" in btn.inner_text().lower():
                    btn.click()
                    break
            except Exception:
                pass
        time.sleep(1)
        find_click(page, "self-hosted")
        time.sleep(2)

        # Fill server URL
        page.fill("#self_hosted_env_settings_form_input_base_url", SERVER_URL)
        time.sleep(0.5)
        page.click("button:has-text('Save')", timeout=5000)
        time.sleep(3)
        ss(page, f"{browser_name.lower().replace(' ', '_')}_02_server_saved")

        # Enter email
        page.fill("input[type='email']", EMAIL)
        time.sleep(0.5)
        page.locator("button:has-text('Continue')").click(timeout=10000)
        time.sleep(4)
        ss(page, f"{browser_name.lower().replace(' ', '_')}_03_email")

        # Enter master password
        page.fill("input[type='password']", PASSWORD)
        time.sleep(0.5)
        try:
            page.locator("button:has-text('Log in with master password')").click(timeout=5000)
        except Exception:
            page.locator("button[type='submit']").last.click(timeout=5000)

        time.sleep(10)
        ss(page, f"{browser_name.lower().replace(' ', '_')}_04_vault")
        final = page.evaluate("() => document.body.innerText")
        print(f"  Vault text: {final[:300]}")

        context.close()

    success = any(w in final.lower() for w in ["vault", "all items", "welcome to your vault"])
    print(f"  {'SUCCESS' if success else 'UNCLEAR'}")
    return success


def configure_firefox() -> bool:
    """Configure Bitwarden in Firefox using Selenium + geckodriver."""
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import TimeoutException

    print(f"\n{'='*60}")
    print("Configuring Firefox ESR")
    print(f"{'='*60}")

    if not Path(FIREFOX_XPI).exists():
        print(f"  ERROR: Firefox XPI not found at {FIREFOX_XPI}")
        return False

    options = Options()
    options.headless = False
    service = Service(executable_path=GECKODRIVER, log_output="/tmp/bw_geckodriver.log")
    driver = webdriver.Firefox(service=service, options=options)
    driver.set_page_load_timeout(30)

    def find_click(text):
        for tag in ["button", "li", "a"]:
            els = driver.find_elements(By.TAG_NAME, tag)
            for el in els:
                try:
                    if el.is_displayed() and text.lower() in el.text.strip().lower():
                        el.click()
                        return True
                except Exception:
                    pass
        return False

    def get_body():
        try:
            return driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return ""

    try:
        # Install extension temporarily
        print("  Installing Bitwarden XPI...")
        driver.install_addon(FIREFOX_XPI, temporary=True)
        time.sleep(5)

        # Get the internal UUID assigned by this Firefox instance
        try:
            driver.get("about:debugging#/runtime/this-firefox")
        except TimeoutException:
            pass
        time.sleep(3)

        body = get_body()
        bw_idx = body.find("Bitwarden")
        section = body[bw_idx : bw_idx + 500] if bw_idx >= 0 else ""
        uuid_m = re.search(
            r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", section
        )
        ext_uuid = uuid_m.group(1) if uuid_m else None
        print(f"  Extension UUID: {ext_uuid}")

        if not ext_uuid:
            print("  ERROR: Could not find extension UUID")
            driver.quit()
            return False

        popup_url = f"moz-extension://{ext_uuid}/popup/index.html"

        # Close the bitwarden.com/browser-start window
        for h in driver.window_handles:
            driver.switch_to.window(h)
            if "bitwarden.com" in driver.current_url:
                driver.close()
                break
        driver.switch_to.window(driver.window_handles[0])

        # Navigate to popup (30s timeout is enough for the Angular app to boot)
        print(f"  Opening popup: {popup_url}")
        try:
            driver.get(popup_url)
        except TimeoutException:
            print("  Navigation timed out (normal for extension popup)")

        time.sleep(6)
        ss(driver, "firefox_01_initial")
        body = get_body()
        print(f"  Initial page: {body[:200]}")

        # Click Log in if on intro page
        if "Security, prioritized" in body or "Create account" in body:
            print("  Clicking Log in...")
            find_click("Log in")
            time.sleep(3)

        # Click env selector -> self-hosted
        btns = driver.find_elements(By.TAG_NAME, "button")
        for btn in btns:
            try:
                if btn.is_displayed() and "bitwarden.com" in btn.text.strip().lower():
                    btn.click()
                    print("  Clicked env selector")
                    break
            except Exception:
                pass
        time.sleep(1)
        find_click("self-hosted")
        print("  Clicked self-hosted")
        time.sleep(3)

        # Fill server URL
        url_field = driver.find_element(By.ID, "self_hosted_env_settings_form_input_base_url")
        url_field.clear()
        url_field.send_keys(SERVER_URL)
        print(f"  Entered URL: {SERVER_URL}")
        ss(driver, "firefox_02_url_entered")
        find_click("Save")
        time.sleep(3)
        ss(driver, "firefox_03_saved")
        body = get_body()
        print(f"  After save: {body[:200]}")

        # Enter email
        print("  Entering email...")
        email_f = driver.find_element(By.CSS_SELECTOR, "input[type='email']")
        email_f.clear()
        email_f.send_keys(EMAIL)
        ss(driver, "firefox_04_email")
        find_click("Continue")
        time.sleep(5)
        ss(driver, "firefox_05_after_continue")

        # Enter master password
        print("  Entering master password...")
        pw_f = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pw_f.clear()
        pw_f.send_keys(PASSWORD)
        ss(driver, "firefox_06_password")

        if not find_click("Log in with master password"):
            subs = [
                el
                for el in driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")
                if el.is_displayed()
            ]
            if subs:
                subs[-1].click()

        time.sleep(15)
        ss(driver, "firefox_07_vault")
        final = get_body()
        print(f"  After login: {final[:400]}")

        driver.quit()

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback

        traceback.print_exc()
        try:
            ss(driver, "firefox_error")
            driver.quit()
        except Exception:
            pass
        return False

    success = any(w in final.lower() for w in ["vault", "all items", "welcome to your vault"])
    print(f"  {'SUCCESS' if success else 'UNCLEAR'}")
    return success


def main():
    print("Bitwarden Extension Configuration")
    print(f"Server: {SERVER_URL}")
    print(f"Email:  {EMAIL}")
    print(f"Screenshots: {SCREENSHOTS}")

    results = {}

    if Path(CHROME_EXT_PATH).exists():
        results["Google Chrome"] = configure_chromium_based("Google Chrome", CHROME_EXT_PATH)
    else:
        print(f"\nSkipping Google Chrome: {CHROME_EXT_PATH} not found")
        results["Google Chrome"] = False

    if Path(CHROMIUM_EXT_PATH).exists():
        results["Chromium"] = configure_chromium_based("Chromium", CHROMIUM_EXT_PATH)
    else:
        print(f"\nSkipping Chromium: {CHROMIUM_EXT_PATH} not found")
        results["Chromium"] = False

    results["Firefox ESR"] = configure_firefox()

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    for browser, ok in results.items():
        print(f"  {browser}: {'SUCCESS' if ok else 'NEEDS MANUAL CHECK'}")
    print(f"\nScreenshots: {SCREENSHOTS}")


if __name__ == "__main__":
    main()

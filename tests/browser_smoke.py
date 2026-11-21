from argparse import ArgumentParser
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = ArgumentParser(description="Playwright acceptance test. Use a fresh, isolated server profile.")
parser.add_argument("--url", default="http://127.0.0.1:8080")
parser.add_argument("--fixture", type=Path, required=True)
parser.add_argument("--out", type=Path, default=Path("out/verify-browser"))
options = parser.parse_args()
URL = options.url.rstrip("/")
OUT = options.out
OUT.mkdir(parents=True, exist_ok=True)
FIXTURE = options.fixture
if not FIXTURE.is_file():
    parser.error("Provide a compatible NGU Idle .txt save fixture.")
OWNER = "b" * 48

def ready(page):
    page.goto(URL)
    page.locator("#game[width], #failure:not([hidden])").first.wait_for(timeout=20000)
    assert page.locator("#failure").is_hidden(), page.locator("#failure").text_content()
    page.wait_for_timeout(300)

def click(page, x, y):
    page.locator("#game").click(position={"x": x, "y": y})
    page.wait_for_timeout(400)

def profile(page):
    return page.request.get(URL + "/api/profile", headers={"X-NGU-Owner": OWNER}).json()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": 1280, "height": 720}, accept_downloads=True)
    errors = []
    requests = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: print(m.type, m.text[:2000], flush=True) if m.type in ("warning", "error") else None)
    page.on("request", lambda req: requests.append(req.url))
    ready(page)
    assert page.locator("header, footer, dialog").count() == 0
    page.screenshot(path=OUT/"home-full.png")
    for x, y in [(940, 486), (488, 261), (1091, 379)]:
        click(page, x, y)
    page.locator("#game-text").wait_for(state="visible", timeout=4000)
    page.locator("#game-text").fill("Browser Bob")
    page.locator("#game-text").press("Enter")
    click(page, 1091, 456)
    page.screenshot(path=OUT/"intro.png")
    data = profile(page)
    assert len(data["files"].get("slots/slot-1.txt", "")) > 10_000, (list(data["files"]), page.locator("#failure").text_content(), errors)
    assert 1 in data["locked"], data["locked"]
    print("New save writes Slot 1, lock visible independently.", flush=True)
    click(page, 640, 615)
    page.screenshot(path=OUT/"playing.png")
    click(page, 150, 578)
    click(page, 488, 526)
    page.screenshot(path=OUT/"load-empty.png")
    page.locator("#save-file").wait_for(state="visible", timeout=4000)
    with page.expect_file_chooser() as chooser:
        page.locator("#save-file").click()
    chooser.value.set_files(FIXTURE)
    page.wait_for_timeout(1100)
    assert "".join(FIXTURE.read_text().split()) == profile(page)["files"]["slots/slot-5.txt"]
    print("Desktop save passed native menu import and byte comparison.", flush=True)

    phone = browser.new_page(viewport={"width": 844, "height": 475}, device_scale_factor=1.5, has_touch=True, is_mobile=True)
    ready(phone)
    phone.locator("#game").tap(position={"x": 218, "y": 322})
    phone.wait_for_timeout(450)
    phone.screenshot(path=OUT/"mobile-locked.png")
    page.wait_for_timeout(1750)
    assert page.request.post(URL + "/api/claim", headers={"Origin": URL}, data={"slot": 1, "owner": OWNER}).status == 423
    page.context.set_offline(True)
    page.wait_for_timeout(3500)
    click(page, 58, 578)
    page.screenshot(path=OUT/"offline.png")
    with page.expect_download(timeout=5000) as download:
        click(page, 1091, 475)
    download.value.save_as(OUT/"current-offline.txt")
    assert (OUT/"current-offline.txt").stat().st_size > 10000
    assert not any(request.endswith("/api/frame") for request in requests)
    assert not errors, errors
    print("Browser exports live game while offline; no frame API.", flush=True)
    page.context.set_offline(False)
    page.bring_to_front()
    page.wait_for_timeout(2400)
    orig = profile(page)["revisions"]["slots/slot-1.txt"]
    click(page, 1090, 412)
    page.wait_for_timeout(2300)
    assert profile(page)["revisions"]["slots/slot-1.txt"] != orig
    print("Manual save in the active slot persisted without a confirmation.", flush=True)

    with page.expect_response(lambda r: r.url.endswith("/api/autosave") and r.ok, timeout=45_000):
        pass
    state = profile(page)
    assert state["autoSlot"] == 1 and state["locked"] == [0, 1], state["locked"]
    print("Timed autosave belongs to Slot 1 and blocks the autosave alias too.", flush=True)
    click(page, 444, 45)
    click(page, 1090, 412)
    page.wait_for_timeout(2300)
    assert 1 not in profile(page)["locked"], profile(page)["locked"]

    phone = browser.new_page(viewport={"width": 844, "height": 475}, device_scale_factor=1.5, has_touch=True, is_mobile=True)
    phone.add_init_script("""const RealDate = Date; window.Date = class extends RealDate {
      constructor(...args) { super(...(args.length ? args : [RealDate.now() + 7200000])); }
      static now() { return RealDate.now() + 7200000; }
    };""")
    ready(phone)
    phone.locator("#game").tap(position={"x": 422, "y": 322})
    phone.wait_for_timeout(2400)
    phone.screenshot(path=OUT/"mobile-resume.png")
    assert profile(page)["autoSlot"] == 1 and 1 in profile(page)["locked"]
    phone.locator("#game").tap(position={"x": 264, "y": 444})
    phone.wait_for_timeout(450)
    phone.screenshot(path=OUT / "mobile-playing.png")
    print("Phone took the released slot by tapping Load Autosave after two simulated hours.", flush=True)
    browser.close()

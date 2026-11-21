"""Check the game's own tooltip text through the browser's pointer input."""

from argparse import ArgumentParser
from pathlib import Path
from time import monotonic

from playwright.sync_api import sync_playwright


parser = ArgumentParser(description="Use an isolated server with an audit build of ngu-browser.")
parser.add_argument("--url", default="http://127.0.0.1:8085")
parser.add_argument("--fixture", type=Path, required=True, help="Unlocked save with the tutorial finished.")
parser.add_argument("--out", type=Path, default=Path("out/verify-hover"))
args = parser.parse_args()
url = args.url.rstrip("/")
args.out.mkdir(parents=True, exist_ok=True)


def instrument(route):
    response = route.fetch()
    source = response.text().replace("export async function boot(", "async function bootEngine(", 1)
    route.fulfill(response=response, body=source + """
export async function boot(...options) {
  const engine = await bootEngine(...options);
  window.hoverTest = steps => engine.command([{ type: "audit", steps }])[0];
  return engine;
}
""")


def await_tooltip(page, expected):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        text = page.evaluate("() => hoverTest([]).text.map(([, text]) => text)")
        tooltip = any(line.startswith("<b>Current Rebirth Time:</b>\n") and "Autosave in" not in line for line in text)
        if tooltip == expected:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"Rebirth tooltip visibility did not become {expected}")


def load(page, slot, touch=False):
    page.route(url + "/engine.js", instrument)
    page.goto(url)
    canvas = page.locator("#game[width]")
    canvas.wait_for(timeout=20000)
    scale = page.viewport_size["width"] / 1280
    click = canvas.tap if touch else canvas.click
    for x, y in [(333, 485), (488, 261 + 59 * (slot - 1)), (1091, 413), (398, 673)]:
        click(position={"x": x * scale, "y": y * scale})
        page.wait_for_timeout(450)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    desktop = browser.new_page(viewport={"width": 1280, "height": 720})
    save = args.fixture.read_text(encoding="utf-8").strip()
    owner = "a" * 48
    profile = desktop.request.get(url + "/api/profile", headers={"X-NGU-Owner": owner}).json()
    keys = ["slots/slot-4.txt", "slots/slot-5.txt"]
    assert all(profile["files"].get(key, save) == save for key in keys), "Use an isolated profile"
    changes = {key: {"value": save, "revision": None} for key in keys if key not in profile["files"]}
    if changes:
        reply = desktop.request.post(url + "/api/write", headers={"Origin": url}, data={
            "owner": owner, "slot": None, "changes": changes,
        })
        assert reply.ok, reply.text()
    load(desktop, 5)
    await_tooltip(desktop, False)
    desktop.mouse.move(103, 508)
    await_tooltip(desktop, True)
    desktop.screenshot(path=args.out / "mouse.png")
    desktop.mouse.move(335, 710)
    await_tooltip(desktop, False)
    desktop.mouse.move(103, 508)
    await_tooltip(desktop, True)
    desktop.set_viewport_size({"width": 250, "height": 900})
    await_tooltip(desktop, False)
    desktop.screenshot(path=args.out / "resize.png")
    desktop.close()

    phone = browser.new_page(viewport={"width": 844, "height": 475}, has_touch=True, is_mobile=True)
    load(phone, 4, touch=True)
    await_tooltip(phone, False)
    cdp = phone.context.new_cdp_session(phone)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 68, "y": 335}]})
    phone.wait_for_timeout(700)
    await_tooltip(phone, True)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    await_tooltip(phone, True)
    assert any("Attack Skills" in label for _, label in phone.evaluate("() => hoverTest([]).text"))
    phone.screenshot(path=args.out / "touch.png")
    phone.locator("#game").tap(position={"x": 220, "y": 465})
    await_tooltip(phone, False)
    browser.close()

print("Native tooltips passed mouse enter and exit, resize, and touch hold without a click.")

import sys
from playwright.sync_api import sync_playwright
svg_path, out = sys.argv[1], sys.argv[2]
html = "<!doctype html><html><body style='margin:0'>" + open(svg_path).read() + "</body></html>"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1240,"height":700})
    pg.set_content(html)
    pg.wait_for_timeout(400)
    el = pg.query_selector("svg")
    el.screenshot(path=out)
    b.close()
print("wrote", out)

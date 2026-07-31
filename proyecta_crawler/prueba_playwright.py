from playwright.sync_api import sync_playwright

URL = "https://www.ellagar.com/ECOMMERCE/CategoriaArticulo/9/0/construcci%C3%B3n"

with sync_playwright() as p:

    browser = p.chromium.launch(headless=False)

    page = browser.new_page()

    print("🚀 Abriendo El Lagar...")

    page.goto(URL)

    page.wait_for_timeout(5000)

    print("Título:", page.title())

    html = page.content()

    with open("ellagar.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ HTML guardado.")

    browser.close()
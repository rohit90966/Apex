import asyncio
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page()
        await page.goto("https://www.saucedemo.com/")
        await page.fill("#user-name", "standard_user")
        await page.fill("#password", "secret_sauce")
        await page.click("#login-button")
        await page.click("#add-to-cart-sauce-labs-backpack")
        await page.click("[data-test='shopping-cart-link']")
        await page.click("#checkout")
        await page.fill("#first-name", "Alex")
        await page.fill("#last-name", "Tester")
        await page.fill("#postal-code", "90210")
        await page.click("#continue")
        print("Current URL:", page.url)
        finish_btn = await page.query_selector("#finish")
        print("finish_btn:", finish_btn)
        if finish_btn:
            is_vis = await finish_btn.is_visible()
            print("is_visible:", is_vis)
        html = await page.content()
        with open("scratch/checkout_step_two.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Wrote checkout_step_two.html")
        await page.click("#finish")
        print("URL after clicking finish:", page.url)
        content_after = await page.content()
        print("Contains 'Thank you for your order':", "Thank you for your order" in content_after)
        await b.close()

if __name__ == "__main__":
    asyncio.run(check())

import os
import json
import hashlib
import requests
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
SEEN_PRODUCTS_FILE = "seen_products.json"
AVAILABLE_PRODUCTS_FILE = "available_products.json"

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(data, filename):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def send_discord_message(message):
    if not DISCORD_WEBHOOK:
        print("Ingen Discord-webhook angiven.")
        return
    payload = { "content": message }
    headers = { "Content-Type": "application/json" }
    response = requests.post(DISCORD_WEBHOOK, json=payload, headers=headers)
    if response.status_code == 204:
        print("✅ Discord-notis skickad.")
    else:
        print(f"❌ Fel vid skickande: {response.status_code}, {response.text}")

def scrape_site(playwright, site, seen_products, available_products):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    print(f"🔍 Besöker {site['url']}")
    page.goto(site["url"], timeout=60000)
    page.wait_for_timeout(3000)

    elements = page.locator(site["selector"]).all()
    new_seen = []
    for el in elements:
        title = el.inner_text().strip()
        if any(keyword.lower() in title.lower() for keyword in site["keywords"]):
            title_hash = hashlib.sha256(title.encode()).hexdigest()
            new_seen.append(title_hash)
            if title_hash not in seen_products:
                send_discord_message(f"🆕 Ny produkt upptäckt på {site['name']}: {title}\n{site['url']}")
                seen_products[title_hash] = title
            elif title_hash not in available_products:
                # Nytt tillgängligt exemplar av en känd produkt
                send_discord_message(f"♻️ Tillgänglig igen: {title}\n{site['url']}")
                available_products[title_hash] = title
    browser.close()

    return seen_products, available_products

def main():
    seen_products = load_json(SEEN_PRODUCTS_FILE)
    available_products = load_json(AVAILABLE_PRODUCTS_FILE)

    with open("sites.json") as f:
        sites = json.load(f)

    with sync_playwright() as p:
        for site in sites:
            seen_products, available_products = scrape_site(p, site, seen_products, available_products)

    save_json(seen_products, SEEN_PRODUCTS_FILE)
    save_json(available_products, AVAILABLE_PRODUCTS_FILE)

if __name__ == "__main__":
    main()

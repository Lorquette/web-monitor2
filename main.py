import os
import json
import hashlib
from playwright.sync_api import sync_playwright
import re
import time

DATA_DIR = "data"
SEEN_PRODUCTS_FILE = os.path.join(DATA_DIR, "seen_products.json")
AVAILABLE_PRODUCTS_FILE = os.path.join(DATA_DIR, "available_products.json")
SITES_FILE = "sites.json"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

KEYWORDS = [
    "Pokémon", "Pokemon", "Destined Rivals", "Prismatic Evolutions"
]

def load_json(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_json(file_path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def hash_string(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def send_discord_message(message):
    import requests
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set in environment variable.")
        return
    payload = {"content": message}
    response = requests.post(DISCORD_WEBHOOK, json=payload)
    if response.status_code != 204:
        print(f"Failed to send Discord message: {response.status_code} {response.text}")

def product_matches_keywords(name):
    return any(re.search(keyword, name, re.IGNORECASE) for keyword in KEYWORDS)

def scroll_to_load_all(page):
    """Scrolla ned på sidan successivt tills inget mer innehåll laddas."""
    previous_height = page.evaluate("document.body.scrollHeight")
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)  # Vänta så att nytt innehåll laddas
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == previous_height:
            break
        previous_height = new_height

def scrape_site(site, seen_products, available_products):
    url = site["url"]
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    availability_selector = site["availability_selector"]
    availability_in_stock = site.get("availability_in_stock", ["i lager", "in stock", "available"])
    
    new_seen = False
    new_available = False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, timeout=60000)

        # Scrolla för att ladda dynamiskt innehåll (om sidan gör så)
        scroll_to_load_all(page)

        products = page.locator(product_selector)
        count = products.count()

        for i in range(count):
            try:
                product_elem = products.nth(i)
                name = product_elem.locator(name_selector).inner_text().strip()
                availability_text = product_elem.locator(availability_selector).inner_text().strip().lower()

                if not product_matches_keywords(name):
                    continue

                product_hash = hash_string(name)

                # Kolla om produkt är ny (inte sett tidigare)
                if product_hash not in seen_products:
                    seen_products[product_hash] = name
                    new_seen = True
                    send_discord_message(f"**Ny produkt hittad:** {name} ({url})")
                
                # Kolla om produkt är tillgänglig (lagerstatus)
                in_stock = any(keyword in availability_text for keyword in availability_in_stock)
                was_available = product_hash in available_products

                if in_stock and not was_available:
                    available_products[product_hash] = name
                    new_available = True
                    send_discord_message(f"**Produkt åter i lager:** {name} ({url})")

                elif not in_stock and was_available:
                    # Om produkten är slut i lager, ta bort från available_products
                    del available_products[product_hash]

            except Exception as e:
                print(f"Fel vid hantering av produkt {i} på {url}: {e}")

        browser.close()
    return new_seen or new_available

def main():
    seen_products = load_json(SEEN_PRODUCTS_FILE)
    available_products = load_json(AVAILABLE_PRODUCTS_FILE)
    sites = load_json(SITES_FILE)

    if not sites:
        print("Ingen sites.json hittades eller den är tom.")
        return

    any_changes = False

    for site in sites:
        print(f"Skannar: {site.get('name', site['url'])}")
        changed = scrape_site(site, seen_products, available_products)
        any_changes = any_changes or changed

    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)

    if not any_changes:
        print("Inga nya eller återkommande produkter upptäcktes.")

if __name__ == "__main__":
    main()

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

def scroll_to_load_all(page, product_selector):
    import time

    previous_count = 0
    max_attempts = 10
    attempts = 0

    while attempts < max_attempts:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        current_count = page.locator(product_selector).count()
        print(f"Scrollförsök {attempts + 1}: {current_count} produkter")

        if current_count == previous_count:
            print("Inga fler produkter laddades.")
            break

        previous_count = current_count
        attempts += 1
    time.sleep(2)
    
def scrape_site(site, seen_products, available_products):
    # Läs ut nödvändiga selectors och inställningar från sites.json
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    availability_selector = site["availability_selector"]
    availability_in_stock = site.get("availability_in_stock", ["i lager", "in stock", "available"])
    url_pattern = site.get("url_pattern")
    start_page = site.get("start_page", 1)
    max_pages = site.get("max_pages", 1)

    new_seen = False   # Flagga för om nya produkter hittades
    new_available = False  # Flagga för om produkter blivit tillgängliga igen

    with sync_playwright() as p:
        # Starta Chromium-browser för att kunna interagera med sidan
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43")

        # Funktion för att kolla om produkt går att förbeställa på produktsidan
        def check_if_preorderable(product_url):
            try:
                page.goto(product_url, timeout=5000)
                time.sleep(2)  # Vänta att sidan laddas
                buy_buttons = page.locator(site["buy_button_selector"])
                return buy_buttons.count() > 0
            except Exception as e:
                print(f"Fel vid kontroll av förbeställning på {product_url}: {e}")
                return False

        # Loopar igenom alla sidor som ska skannas enligt url_pattern
        for page_num in range(start_page, start_page + max_pages):
            if url_pattern:
                url = url_pattern.format(page=page_num)
            else:
                url = site.get("url")
            print(f"Hämtar: {url}")
            page.goto(url, timeout=10000)

            # Scrolla för att ladda alla produkter dynamiskt
            scroll_to_load_all(page, product_selector)

            # Hämta alla produkter enligt selector
            products = page.locator(product_selector)
            count = products.count()
            print(f"Totalt hittade produkter: {count}")

            # Loopar igenom varje produkt på sidan
            for i in range(count):
                try:
                    product_elem = products.nth(i)
                    name = product_elem.locator(name_selector).inner_text().strip()

                    # Läs tillgänglighetstext, eller tom om ej hittas
                    availability_text = ""
                    try:
                        availability_text = product_elem.locator(availability_selector).first.inner_text().strip().lower()
                    except Exception:
                        pass

                    # Hoppa över produkter som inte matchar nyckelorden
                    if not product_matches_keywords(name):
                        continue

                    product_hash = hash_string(name)

                    # Om produkten är helt ny, markera och skicka notis
                    if product_hash not in seen_products:
                        seen_products[product_hash] = name
                        new_seen = True
                        # Skicka snyggt formaterad Discord-notis för ny produkt
                        send_discord_message(
                            f"🎉 **Ny produkt upptäckt!**\n"
                            f"**Namn:** `{name}`\n"
                            f"**Webbplats:** {site.get('name', url)}\n"
                            f"**Sida:** {url}\n"
                            f"🔍 Kontrollera snabbt innan den försvinner!"
                        )

                    # Kontrollera om produkten ej är släppt än, och i så fall kolla förbeställning på produktsidan
                    is_not_released = False
                    if site.get("check_product_page_if_not_released", False):
                        try:
                            not_released_elem = product_elem.locator(site["not_released_selector"])
                            is_not_released = not_released_elem.count() > 0
                        except Exception:
                            is_not_released = False

                    if is_not_released:
                        product_link = None
                        try:
                            product_link = product_elem.locator(site["product_link_selector"]).get_attribute("href")
                            if product_link and product_link.startswith("/"):
                                base_url = re.match(r"(https?://[^/]+)", url).group(1)
                                product_link = base_url + product_link
                        except Exception:
                            pass

                        if product_link:
                            in_stock = check_if_preorderable(product_link)
                        else:
                            in_stock = False
                    else:
                        # Om produkten är släppt, kolla lagerstatus från produktlistan
                        in_stock = any(keyword in availability_text for keyword in availability_in_stock)

                    was_available = product_hash in available_products

                    # Om produkten är ny i lager, markera och skicka notis
                    if in_stock and not was_available:
                        available_products[product_hash] = name
                        new_available = True
                        send_discord_message(
                            f"✅ **Produkt tillbaka i lager!**\n"
                            f"**Namn:** `{name}`\n"
                            f"**Webbplats:** {site.get('name', url)}\n"
                            f"**Sida:** {url}\n"
                            f"🎯 Skynda att köp innan den tar slut igen!"
                        )
                    # Om produkten ej längre finns i lager men tidigare fanns, ta bort
                    elif not in_stock and was_available:
                        del available_products[product_hash]

                except Exception as e:
                    print(f"Fel vid hantering av produkt {i} på {url}: {e}")

        # Stäng ner browsern när skanningen är klar
        browser.close()

    # Returnera True om något nytt upptäcktes eller åter blev tillgängligt
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
        print(f"Skannar: {site.get('name') or site.get('url') or site.get('url_pattern') or 'Okänd site'}")
        changed = scrape_site(site, seen_products, available_products)
        any_changes = any_changes or changed

    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)

    if not any_changes:
        print("Inga nya eller återkommande produkter upptäcktes.")

if __name__ == "__main__":
    main()

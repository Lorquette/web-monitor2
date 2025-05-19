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

    start = time.time()
    print(f"Scroll-funktionen startar vid {start:.2f} sek")

    previous_count = 0
    max_attempts = 10
    attempts = 0

    while attempts < max_attempts:
        print(f"Innan scrollförsök {attempts + 1}...")
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)", timeout=3000)
            print("Scrollning utförd.")
        except Exception as e:
            print(f"Fel vid scrollning: {e}")
            break  # Avbryt scroll-loop vid fel

        time.sleep(2)  # Vänta så att nya produkter hinner laddas

        try:
            current_count = page.locator(product_selector).count()
            print(f"Scrollförsök {attempts + 1}: {current_count} produkter")
        except Exception as e:
            print(f"Fel vid hämtning av produktantal: {e}")
            break

        if current_count == previous_count:
            print("Inga fler produkter laddades.")
            break

        previous_count = current_count
        attempts += 1

    time.sleep(2)
    end = time.time()
    print(f"Scroll-funktionen avslutades efter {end - start:.2f} sekunder")
    
import time

def scrape_site(site, seen_products, available_products):
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    availability_selector = site["availability_selector"]
    availability_in_stock = site.get("availability_in_stock", ["i lager", "in stock", "available"])

    if "url_pattern" in site:
        urls_to_scrape = [site["url_pattern"].format(page=p) for p in range(start_page, start_page + max_pages)]
    elif "url" in site:
        urls_to_scrape = [site["url"]]
    else:
        print("Ingen giltig URL-konfiguration för siten.")
        return False

    new_seen = False
    new_available = False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43")

        def check_if_preorderable(product_url):
            print(f"Startar preorder-check: {product_url}")
            start_pre = time.time()
            try:
                page.goto(product_url, timeout=5000, wait_until="domcontentloaded")
                print(f"Sida laddad på {time.time()-start_pre:.2f} sek")

                count = page.locator(site["buy_button_selector"]).count(timeout=2000)
                print(f"Antal buy-buttons: {count}")
                return count > 0

            except Exception as e:
                print(f"Fel vid kontroll av förbeställning på {product_url}: {e}")
                return False
            finally:
                print(f"Preorder-check klar på {time.time()-start_pre:.2f} sek")

        for url in urls_to_scrape:
            print(f"\n-- Hämtar: {url} --")
            start_page_load = time.time()
            try:
                page.goto(url, timeout=10000)
                print(f"Sida laddad på {time.time()-start_page_load:.2f} sek")
            except Exception as e:
                print(f"Kunde inte ladda {url}: {e}")
                continue

            print("Startar scrollning för att ladda produkter...")
            scroll_start = time.time()
            print("Innan scroll_to_load_all...")
            scroll_to_load_all(page, product_selector)
            print("Efter scroll_to_load_all...")
            print(f"Scrollning klar efter {time.time()-scroll_start:.2f} sek")

            products = page.locator(product_selector)
            try:
                count = products.count()
                print(f"Totalt hittade produkter: {count}")
            except Exception as e:
                print(f"Fel vid räkning av produkter: {e}")
                continue

            for i in range(count):
                product_start = time.time()
                try:
                    product_elem = products.nth(i)
                    name = product_elem.locator(name_selector).inner_text().strip()
                    print(f"Produkt {i+1}/{count}: {name}")

                    availability_text = ""
                    try:
                        availability_text = product_elem.locator(availability_selector).first.inner_text().strip().lower()
                    except Exception:
                        pass
                    print(f"  Tillgänglighetstext: '{availability_text}'")

                    if not product_matches_keywords(name):
                        print(f"  Hoppar över produkten då den inte matchar nyckelord.")
                        continue

                    product_hash = hash_string(name)

                    if product_hash not in seen_products:
                        print(f"  Ny produkt upptäckt!")
                        seen_products[product_hash] = name
                        new_seen = True
                        send_discord_message(
                            f"🎉 **Ny produkt upptäckt!**\n"
                            f"**Namn:** `{name}`\n"
                            f"**Webbplats:** {site.get('name', url)}\n"
                            f"**Sida:** {url}\n"
                            f"🔍 Kontrollera snabbt innan den försvinner!"
                        )

                    is_not_released = False
                    if site.get("check_product_page_if_not_released", False):
                        try:
                            not_released_elem = product_elem.locator(site["not_released_selector"])
                            is_not_released = not_released_elem.count() > 0
                        except Exception:
                            is_not_released = False
                    print(f"  Är produkten inte släppt än? {is_not_released}")

                    if is_not_released:
                        product_link = None
                        try:
                            product_link = product_elem.locator(site["product_link_selector"]).get_attribute("href")
                            if product_link and product_link.startswith("/"):
                                base_url = re.match(r"(https?://[^/]+)", url).group(1)
                                product_link = base_url + product_link
                        except Exception:
                            pass
                        print(f"  Produktlänk: {product_link}")

                        if product_link:
                            in_stock = check_if_preorderable(product_link)
                        else:
                            in_stock = False
                    else:
                        in_stock = any(keyword in availability_text for keyword in availability_in_stock)
                    print(f"  I lager (eller preorderbar): {in_stock}")

                    was_available = product_hash in available_products

                    if in_stock and not was_available:
                        print(f"  Produkten är tillbaka i lager!")
                        available_products[product_hash] = name
                        new_available = True
                        send_discord_message(
                            f"✅ **Produkt tillbaka i lager!**\n"
                            f"**Namn:** `{name}`\n"
                            f"**Webbplats:** {site.get('name', url)}\n"
                            f"**Sida:** {url}\n"
                            f"🎯 Skynda att köp innan den tar slut igen!"
                        )
                    elif not in_stock and was_available:
                        print(f"  Produkten finns inte längre i lager, tas bort.")
                        del available_products[product_hash]

                except Exception as e:
                    print(f"Fel vid hantering av produkt {i} på {url}: {e}")
                finally:
                    print(f"  Hantering av produkt {i+1} klar på {time.time()-product_start:.2f} sek")

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
        print(f"Skannar: {site.get('name') or site.get('url') or site.get('url_pattern') or 'Okänd site'}")
        changed = scrape_site(site, seen_products, available_products)
        any_changes = any_changes or changed

    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)

    if not any_changes:
        print("Inga nya eller återkommande produkter upptäcktes.")

if __name__ == "__main__":
    main()

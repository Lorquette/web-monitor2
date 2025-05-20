import os
import json
import hashlib
from playwright.sync_api import sync_playwright
import re
import time
import requests

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
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set in environment variable.", flush=True)
        return
    payload = {"content": message}
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload)
        if response.status_code != 204:
            print(f"Failed to send Discord message: {response.status_code} {response.text}", flush=True)
    except Exception as e:
        print(f"Exception while sending Discord message: {e}", flush=True)

def product_matches_keywords(name):
    return any(re.search(keyword, name, re.IGNORECASE) for keyword in KEYWORDS)

def scroll_to_load_all(page, product_selector):
    start = time.time()
    print(f"Scroll-funktionen startar vid {start:.2f} sek", flush=True)

    previous_count = 0
    max_attempts = 10
    attempts = 0

    while attempts < max_attempts:
        print(f"Innan scrollförsök {attempts + 1}...", flush=True)
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            print("Scrollning utförd.", flush=True)
        except Exception as e:
            print(f"Fel vid scrollning: {e}", flush=True)
            break

        time.sleep(2)

        try:
            current_count = page.locator(product_selector).count()
            print(f"Scrollförsök {attempts + 1}: {current_count} produkter", flush=True)
        except Exception as e:
            print(f"Fel vid hämtning av produktantal: {e}", flush=True)
            break

        if current_count == previous_count:
            print("Inga fler produkter laddades.", flush=True)
            break

        previous_count = current_count
        attempts += 1

    time.sleep(2)
    end = time.time()
    print(f"Scroll-funktionen avslutades efter {end - start:.2f} sekunder", flush=True)

def scrape_site(site, seen_products, available_products):
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    availability_selector = site["availability_selector"]
    availability_in_stock = site.get("availability_in_stock", ["i lager", "in stock", "available"])

    if "url_pattern" in site:
        urls_to_scrape = [site["url_pattern"].format(page=p) for p in range(site.get("start_page", 1), site.get("start_page", 1) + site.get("max_pages", 1))]
    elif "url" in site:
        urls_to_scrape = [site["url"]]
    else:
        print("Ingen giltig URL-konfiguration för siten.", flush=True)
        return False

    new_seen = False
    new_available = False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43")

        def check_if_preorderable(product_url):
            print(f"Startar preorder-check: {product_url}", flush=True)
            start_pre = time.time()
            try:
                page.goto(product_url, timeout=5000, wait_until="domcontentloaded")
                print(f"Sida laddad på {time.time()-start_pre:.2f} sek", flush=True)

                count = page.locator(site["buy_button_selector"]).count()
                print(f"Antal buy-buttons: {count}", flush=True)
                return count > 0

            except Exception as e:
                print(f"Fel vid kontroll av förbeställning på {product_url}: {e}", flush=True)
                return False
            finally:
                print(f"Preorder-check klar på {time.time()-start_pre:.2f} sek", flush=True)

        for url in urls_to_scrape:
            print(f"\n-- Hämtar: {url} --", flush=True)
            start_page_load = time.time()
            try:
                page.goto(url, timeout=10000)
                print(f"Sida laddad på {time.time()-start_page_load:.2f} sek", flush=True)
            except Exception as e:
                print(f"Kunde inte ladda {url}: {e}", flush=True)
                continue

            print("Startar scrollning för att ladda produkter...", flush=True)
            scroll_start = time.time()
            scroll_to_load_all(page, product_selector)
            print(f"Scrollning klar efter {time.time()-scroll_start:.2f} sek", flush=True)

            products = page.locator(product_selector)
            try:
                count = products.count()
                print(f"Totalt hittade produkter: {count}", flush=True)
            except Exception as e:
                print(f"Fel vid räkning av produkter: {e}", flush=True)
                continue

            for i in range(count):
                product_start = time.time()
                try:
                    product_elem = products.nth(i)
                    name = product_elem.locator(name_selector).text_content(timeout=2000).strip()
                    print(f"Produkt {i+1}/{count}: {name}", flush=True)

                    availability_text = ""
                    try:
                        availability_text = product_elem.locator(availability_selector).first.inner_text().strip().lower()
                    except Exception:
                        pass
                    print(f"  Tillgänglighetstext: '{availability_text}'", flush=True)

                    if not product_matches_keywords(name):
                        print(f"  Hoppar över produkten då den inte matchar nyckelord.", flush=True)
                        continue

                    product_hash = hash_string(name)

                    if product_hash not in seen_products:
                        print(f"  Ny produkt upptäckt!", flush=True)
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
                    print(f"  Är produkten inte släppt än? {is_not_released}", flush=True)

                    if is_not_released:
                        product_link = None
                        try:
                            product_link = product_elem.locator(site["product_link_selector"]).get_attribute("href")
                            if product_link and product_link.startswith("/"):
                                base_url = re.match(r"(https?://[^/]+)", url).group(1)
                                product_link = base_url + product_link
                        except Exception:
                            pass
                        print(f"  Produktlänk: {product_link}", flush=True)

                        if product_link:
                            in_stock = check_if_preorderable(product_link)
                        else:
                            in_stock = False
                    else:
                        in_stock = any(keyword in availability_text for keyword in availability_in_stock)
                    print(f"  I lager (eller preorderbar): {in_stock}", flush=True)

                    was_available = product_hash in available_products

                    if in_stock and not was_available:
                        print(f"  Produkten är tillbaka i lager!", flush=True)
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
                        print(f"  Produkten finns inte längre i lager, tas bort.", flush=True)
                        del available_products[product_hash]

                except Exception as e:
                    print(f"Fel vid hantering av produkt {i} på {url}: {e}", flush=True)
                finally:
                    print(f"  Hantering av produkt {i+1} klar på {time.time()-product_start:.2f} sek", flush=True)

        browser.close()

    return new_seen or new_available
    
def get_all_products(site):
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    availability_selector = site["availability_selector"]

    products_list = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                                           "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43")

        url = site.get("url") or (site.get("url_pattern").format(page=site.get("start_page", 1)) if "url_pattern" in site else None)
        if not url:
            print("Ingen giltig URL att hämta produkter ifrån.", flush=True)
            browser.close()
            return []

        try:
            page.goto(url, timeout=10000)
            scroll_to_load_all(page, product_selector)
            products = page.locator(product_selector)
            count = products.count()
        except Exception as e:
            print(f"Fel vid hämtning av produkter: {e}", flush=True)
            browser.close()
            return []

        for i in range(count):
            try:
                product_elem = products.nth(i)
                name = product_elem.locator(name_selector).text_content(timeout=2000).strip()
                availability_text = ""
                try:
                    availability_text = product_elem.locator(availability_selector).first.inner_text().strip()
                except Exception:
                    pass

                products_list.append({
                    "name": name,
                    "availability_text": availability_text
                })
            except Exception as e:
                print(f"Fel vid hämtning av produkt {i}: {e}", flush=True)

        browser.close()

    return products_list
    
def main():
    seen_products = load_json(SEEN_PRODUCTS_FILE)
    available_products = load_json(AVAILABLE_PRODUCTS_FILE)
    sites = load_json(SITES_FILE)

    if not sites:
        print("Ingen sites.json hittades eller den är tom.", flush=True)
        return

    any_changes = False

    for site in sites:
        print(f"Skannar: {site.get('name') or site.get('url') or site.get('url_pattern') or 'Okänd site'}", flush=True)
        changed = scrape_site(site, seen_products, available_products)
        any_changes = any_changes or changed

    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)

    # --- NYTT: skriv ut alla produkter från första siten för debugging ---
    if sites:
        print("\n--- Alla produkter på första siten ---", flush=True)
        products = get_all_products(sites[0])
        for prod in products:
            print(f"Namn: {prod['name']} | Tillgänglighet: {prod['availability_text']}", flush=True)

    if not any_changes:
        print("Inga nya eller återkommande produkter upptäcktes.", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"🚨 Fel i main(): {e}", flush=True)

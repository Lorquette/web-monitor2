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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43"

KEYWORDS = [
    "Pokémon", "Pokemon", "Destined Rivals", "Prismatic Evolutions", "Journey Together", "Palafin"
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

def send_discord_message(name, url, price, status, site_name):
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set in environment variable.", flush=True)
        return
    
    color_map = {
        "Ny produkt": 0x00FF00,           # Grön
        "Tillbaka i lager": 0xFFFF00,     # Gul
        "Förbeställningsbar": 0x1E90FF    # Blå
    }

    embed = {
        "title": name,
        "url": url,
        "color": color_map.get(status, 0x000000),  # Svart fallback
        "fields": [
            {"name": "Pris", "value": price or "Okänt", "inline": True},
            {"name": "Status", "value": status, "inline": True},
            {"name": "Webbplats", "value": site_name, "inline": False},
        ],
        "footer": {"text": "Skynda att köpa innan den tar slut!"}
    }

    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload)
        if response.status_code != 204:
            print(f"Failed to send Discord message: {response.status_code} {response.text}", flush=True)
        time.sleep(1)
        
    except Exception as e:
        print(f"Exception while sending Discord message: {e}", flush=True)

def product_matches_keywords(name):
    return any(re.search(keyword, name, re.IGNORECASE) for keyword in KEYWORDS)

def get_availability_status(product_elem, site):
    # Kolla "i lager" via selector + text
    if site.get("availability_status") is True:
        return "i lager"
    in_stock_selector = site.get("availability_in_stock_selector")
    if in_stock_selector:
        try:
            elems = product_elem.locator(in_stock_selector)
            count = elems.count()
            for i in range(count):
                text = elems.nth(i).inner_text().strip().lower()
                if "i lager" in text or "available" in text or "in stock" in text or "köp" in text or "boka" in text:
                    return "i lager"
        except Exception as e:
            print(f"  Fel vid in_stock_selector: {e}", flush=True)

    # Kolla "slutsåld" via selector + text
    out_of_stock_selector = site.get("availability_out_of_stock_selector")
    out_of_stock_text = site.get("availability_out_of_stock_text", "").lower()
    if out_of_stock_selector:
        try:
            elems = product_elem.locator(out_of_stock_selector)
            count = elems.count()
            for i in range(count):
                text = elems.nth(i).inner_text().strip().lower()
                if out_of_stock_text in text:
                    return "slutsåld"
        except Exception as e:
            print(f"  Fel vid out_of_stock_selector: {e}", flush=True)

    return "okänd"

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
    availability_selector = site.get("availability_selector")  # Behåll för fallback, men används ej längre
    
    if "url_pattern" in site:
        urls_to_scrape = [site["url_pattern"].format(page=p) for p in range(site.get("start_page", 1), site.get("start_page", 1) + site.get("max_pages", 1))]
    elif "url_pattern_complex" in site:
        url_lv1_list = site.get("url_pattern_lv1", [""])
        urls_to_scrape = []

        for lv1 in url_lv1_list:
            for p in range(site.get("start_page", 1), site.get("start_page", 1) + site.get("max_pages", 1)):
                url = site["url_pattern_complex"].format(url_pattern_lv1=lv1, page=p)
                urls_to_scrape.append(url)  
    elif "url" in site:
        urls_to_scrape = [site["url"]]
    elif "urls" in site:
        urls_to_scrape = site["urls"]
    else:
        print("Ingen giltig URL-konfiguration för siten.", flush=True)
        return False

    new_seen = False
    new_available = False

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        product_page = browser.new_page(user_agent=USER_AGENT)
        
        def check_if_preorderable(product_url):
            print(f"Startar preorder-check: {product_url}", flush=True)
            start_pre = time.time()
            try:
                product_page.goto(product_url, timeout=5000, wait_until="domcontentloaded")
                print(f"Sida laddad på {time.time()-start_pre:.2f} sek", flush=True)
        
                count = product_page.locator(site["buy_button_selector"]).count()
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

            if site.get("use_scroll", True):
                print("Startar scrollning för att ladda produkter...", flush=True)
                scroll_start = time.time()
                scroll_to_load_all(page, product_selector)
                print(f"Scrollning klar efter {time.time()-scroll_start:.2f} sek", flush=True)
            else:
                print("Scrollning avaktiverad enligt konfiguration.", flush=True)

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

                    price = None
                    price_selector = site.get("price_selector")
                    if price_selector:
                        try:
                            price = product_elem.locator(price_selector).text_content(timeout=500).strip()
                        except Exception:
                            price = None
                
                    base_url = site.get("base_url", "")
                    product_link_elem = product_elem.locator(site.get("product_link_selector"))
                    product_href = ""
                    try:
                        product_href = product_link_elem.get_attribute("href")
                    except Exception:
                        product_href = None

                    if product_href and not product_href.startswith("http"):
                        product_link = base_url.rstrip("/") + "/" + product_href.lstrip("/")
                    else:
                        product_link = product_href or url
                    
                    name = product_elem.locator(name_selector).text_content(timeout=500).strip()
                    print(f"Produkt {i+1}/{count}: {name}", flush=True)

                    availability_status = get_availability_status(product_elem, site)
                    print(f"  Tillgänglighet: {availability_status}", flush=True)

                    skip_keywords = site.get("skip_keywords", False)
                    
                    if not skip_keywords and not product_matches_keywords(name):
                        print(f"  Hoppar över produkten då den inte matchar nyckelord.", flush=True)
                        continue
                        
                    product_hash = hash_string(name)

                    if product_hash not in seen_products:
                        print(f"  Ny produkt upptäckt!", flush=True)
                        seen_products[product_hash] = name
                        new_seen = True
                        send_discord_message(
                            name=name,
                            url=product_link or url,
                            price=price,
                            status="Ny produkt",
                            site_name=site.get("name", url)
                        )
                        
                    # Börja med att kolla om blå knapp finns (preorderknapp)
                    preorder_selector = site.get("preorder_selector")
                    has_preorder_button = False
                    if preorder_selector:
                        try:
                            preorder_elem = product_elem.locator(preorder_selector)
                            has_preorder_button = preorder_elem.count() > 0
                        except Exception:
                            has_preorder_button = False
                    
                    if has_preorder_button:
                        in_stock = True
                        preorder = True
                        print(f"  Produkten är preorderbar via blå knapp.", flush=True)
                    else:
                        preorder = False
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
                            in_stock = (availability_status == "i lager")
                    
                    print(f"  I lager (eller preorderbar): {in_stock}", flush=True)

                    was_available = product_hash in available_products

                    if in_stock and not was_available:
                        if preorder:
                            status_msg = "Förbeställningsbar"
                        else:
                            status_msg = "Tillbaka i lager"
                    
                        print(f"  Produkten är {status_msg.lower()}!", flush=True)
                        available_products[product_hash] = name
                        seen_products[product_hash] = name
                        new_available = True
                        send_discord_message(
                            name=name,
                            url=product_link or url,
                            price=price,
                            status=status_msg,
                            site_name=site.get("name", url)
                        )

                    elif not in_stock and was_available:
                        print(f"  Produkten finns inte längre i lager, tas bort.", flush=True)
                        del available_products[product_hash]

                except Exception as e:
                    print(f"Fel vid hantering av produkt {i} på {url}: {e}", flush=True)
                finally:
                    print(f"  Hantering av produkt {i+1} klar på {time.time()-product_start:.2f} sek", flush=True)
       
        product_page.close()
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
        print(f"Skannar: {site.get('name') or site.get('url') or site.get('url_pattern') or site.get('url_pattern_complex') or 'Okänd site'}", flush=True)
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

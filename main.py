import asyncio
import aiohttp
import os
import json
import hashlib
from playwright.async_api import async_playwright
import re
import time
import requests
from urllib.parse import urlparse, urlunparse
import google_sheets

DATA_DIR = "data"
SEEN_PRODUCTS_FILE = os.path.join(DATA_DIR, "seen_products.json")
AVAILABLE_PRODUCTS_FILE = os.path.join(DATA_DIR, "available_products.json")
# SITES_FILE = "sites.json"
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_SHEETS_ID_S = os.getenv("GOOGLE_SHEETS_ID_S")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43"

KEYWORDS = [
    "Pokémon", "Pokemon", "Destined Rivals", "Prismatic Evolutions", "Journey Together", "Black Bolt", "White Flare"
]

BLOCKED_KEYWORDS = [
    "Deck Box", "Binder", "Pärm", "Portfolio", "Playmat", "Stacking Tin", "Mugg", "Ryggsäck", "Stort kort", "Ultra Pro"
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


def normalize(text):
    return ' '.join(text.lower().strip().split())


def generate_product_hash(name, site_name):
    return hash_string(f"{normalize(site_name)}|{normalize(name)}")


def clean_product_link(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))


def safe_int(value, default=1):
    try:
        return int(float(value))  # Hanterar även "1.0"
    except (ValueError, TypeError):
        return default

async def send_discord_message(name, url, price, status, site_name):
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set in environment variable.", flush=True)
        return

    color_map = {
        "Ny produkt": 0xFFFF00,           # Gul
        "Tillbaka i lager": 0x00FF00,     # Grön
        "Förbeställningsbar": 0x1E90FF    # Blå
    }

    formatted_name = name.title()

    embed = {
        "title": formatted_name,
        "url": url,
        "color": color_map.get(status, 0x000000),
        "fields": [
            {"name": "Pris", "value": price or "Okänt", "inline": True},
            {"name": "Status", "value": status, "inline": True},
            {"name": "Webbplats", "value": site_name, "inline": False},
        ],
        "footer": {"text": "Skynda att köpa innan den tar slut!"}
    }

    payload = {"embeds": [embed]}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(DISCORD_WEBHOOK, json=payload) as response:
                if response.status != 204:
                    text = await response.text()
                    print(f"Failed to send Discord message: {response.status} {text}", flush=True)
                await asyncio.sleep(1.5)
        except Exception as e:
            print(f"Exception while sending Discord message: {e}", flush=True)

def product_matches_keywords(name):
    name_lower = name.lower()

    for blocked in BLOCKED_KEYWORDS:
        if blocked.lower() in name_lower:
            return False  # Produkter med blockerat ord ska inte matcha

    return any(re.search(keyword, name, re.IGNORECASE) for keyword in KEYWORDS)

def get_urls_to_scrape(site):
    if "url_pattern" in site and site["url_pattern"]:
        start = safe_int(site.get("start_page", 1))
        end = start + safe_int(site.get("max_pages", 1))
        return [site["url_pattern"].format(page=p) for p in range(start, end)]

    elif "url_pattern_complex" in site and site["url_pattern_complex"]:
        start = safe_int(site.get("start_page", 1))
        end = start + safe_int(site.get("max_pages", 1))
        url_lv1_list = site.get("url_pattern_lv1", [""])
        if isinstance(url_lv1_list, str):
            try:
                url_lv1_list = json.loads(url_lv1_list)
            except json.JSONDecodeError:
                print("❌ Fel i url_pattern_lv1-formatet.")
                return []

        urls = []
        for lv1 in url_lv1_list:
            for p in range(start, end):
                urls.append(site["url_pattern_complex"].format(url_pattern_lv1=lv1, page=p))
        return urls

    elif "url" in site and site["url"]:
        return [site["url"]]

    elif "urls" in site and site["urls"]:
        if isinstance(site["urls"], str):
            try:
                return json.loads(site["urls"])
            except json.JSONDecodeError:
                print("❌ Fel i 'urls'-formatet.")
                return []
        else:
            return site["urls"]

    else:
        print("❌ Ingen giltig URL-konfiguration för siten.", flush=True)
        return []

async def get_availability_status(product_elem, site):
    # 1. Tvingad status (t.ex. Samlarhobby)
    if site.get("availability_status") is True:
        return "i lager"

    # 2. In stock
    in_stock_selector = site.get("availability_in_stock_selector")
    if in_stock_selector:
        try:
            elems = product_elem.locator(in_stock_selector)
            count = await elems.count()
            for i in range(count):
                elem = elems.nth(i)
                span = elem.locator("span")
                if await span.count() > 0:
                    text = (await span.first.inner_text()).strip().lower()
                else:
                    text = (await elem.inner_text()).strip().lower()

                if any(word in text for word in ["i lager", "available", "in stock", "köp", "boka", "lägg i varukorg", "preorder", "add to cart", "i lager."]):
                    return "i lager"
        except Exception as e:
            print(f"  Fel vid in_stock_selector: {e}", flush=True)

    # 3. Out of stock
    out_of_stock_selector = site.get("availability_out_of_stock_selector")
    out_of_stock_texts = [t.strip().lower() for t in site.get("availability_out_of_stock_text", "").split(",")]

    if out_of_stock_selector:
        try:
            elems = product_elem.locator(out_of_stock_selector)
            count = await elems.count()
            for i in range(count):
                elem = elems.nth(i)
                span = elem.locator("span")
                if await span.count() > 0:
                    text = (await span.first.inner_text()).strip().lower()
                else:
                    text = (await elem.inner_text()).strip().lower()

                if any(ot in text for ot in out_of_stock_texts):
                    return "slutsåld"
        except Exception as e:
            print(f"  Fel vid out_of_stock_selector: {e}", flush=True)

        try:
            count = await product_elem.locator(out_of_stock_selector).count()
            if count == 0 and site.get("treat_missing_out_of_stock_as_in_stock") is True:
                return "i lager"
        except Exception as e:
            print(f"  Fel vid kontroll av frånvaro av slutsåld-element: {e}", flush=True)

    return "okänd"

async def scroll_to_load_all(page, product_selector, use_mouse_wheel=False):
    print("Startar smart scrollning...", flush=True)
    start = time.time()

    previous_count = 0
    max_attempts = 3
    attempts = 0
    max_duration = 4  # max sekunder totalt
    scroll_start = time.time()

    while attempts < max_attempts and (time.time() - scroll_start) < max_duration:
        try:
            if use_mouse_wheel:
                await page.mouse.wheel(0, 2000)
            else:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception as e:
            print(f"Fel vid scrollning: {e}", flush=True)
            break

        await asyncio.sleep(0.5)

        try:
            current_count = await page.locator(product_selector).count()
            print(f"Scrollförsök {attempts + 1}: {current_count} produkter", flush=True)
        except Exception as e:
            print(f"Fel vid produktantal: {e}", flush=True)
            break

        if current_count == previous_count:
            print("Inga fler produkter laddades – avbryter scroll.", flush=True)
            break

        previous_count = current_count
        attempts += 1

    print(f"Scrollning klar på {time.time() - start:.2f} sekunder\n", flush=True)

async def check_if_preorderable(product_url, product_page, site):
    print(f"Startar preorder-check: {product_url}", flush=True)
    start_pre = time.time()
    try:
        await product_page.goto(product_url, timeout=5000, wait_until="domcontentloaded")
        print(f"Sida laddad på {time.time()-start_pre:.2f} sek", flush=True)

        count = await product_page.locator(site["buy_button_selector"]).count()
        print(f"Antal buy-buttons: {count}", flush=True)
        return count > 0

    except Exception as e:
        print(f"Fel vid kontroll av förbeställning på {product_url}: {e}", flush=True)
        return False
    finally:
        print(f"Preorder-check klar på {time.time()-start_pre:.2f} sek", flush=True)

async def scrape_site(site, seen_products, available_products):
    new_seen = False
    new_available = False
    all_products_hashes = set()
    products_to_update = []  # Lista för batch-uppdatering

    product_selector = site["product_selector"]
    name_selector = site["name_selector"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        main_page = await browser.new_page(user_agent=USER_AGENT)
        preorder_page = await browser.new_page(user_agent=USER_AGENT)

        urls_to_scrape = get_urls_to_scrape(site)
        if not urls_to_scrape:
            print("❌ Ingen giltig URL att skrapa för denna site.", flush=True)
            await preorder_page.close()
            await main_page.close()
            await browser.close()
            return False, set()

        for url in urls_to_scrape:
            print(f"Skrapar sida: {url}", flush=True)
            try:
                await main_page.goto(url, timeout=10000)
                await scroll_to_load_all(main_page, product_selector, site.get("use_mouse_wheel", False))
                products = main_page.locator(product_selector)
                count = await products.count()
            except Exception as e:
                print(f"Fel vid hämtning av produkter på {url}: {e}", flush=True)
                continue  # Gå vidare till nästa URL

            for i in range(count):
                product_start = time.time()
                try:
                    product_elem = products.nth(i)
                    name = normalize(await product_elem.locator(name_selector).text_content(timeout=500))
                    print(f"Produkt {i+1}/{count}: {name}", flush=True)

                    skip_keywords = site.get("skip_keywords", False)

                    if not skip_keywords and not product_matches_keywords(name):
                        print(f"  Hoppar över produkten då den inte matchar nyckelord.", flush=True)
                        continue  # 💨 hoppa tidigt!

                    price = None
                    price_selector = site.get("price_selector")
                    if price_selector:
                        try:
                            price = (await product_elem.locator(price_selector).text_content(timeout=500)).strip()
                        except Exception:
                            price = None

                    base_url = site.get("base_url", "")
                    product_link_elem = product_elem.locator(site.get("product_link_selector"))
                    product_href = None
                    try:
                        product_href = await product_link_elem.get_attribute("href")
                    except Exception:
                        product_href = None

                    if product_href and not product_href.startswith("http"):
                        full_url = base_url.rstrip("/") + "/" + product_href.lstrip("/")
                    else:
                        full_url = product_href or url

                    product_link = clean_product_link(full_url)

                    availability_status = await get_availability_status(product_elem, site)
                    print(f"  Tillgänglighet: {availability_status}", flush=True)

                    product_hash = generate_product_hash(name, site.get("name", ""))
                    all_products_hashes.add(product_hash)

                    if product_hash not in seen_products:
                        seen_products[product_hash] = name
                        new_seen = True
                        await send_discord_message(
                            name=name,
                            url=product_link or url,
                            price=price,
                            status="Ny produkt",
                            site_name=normalize(site.get("name", url))
                        )

                    preorder_selector = site.get("preorder_selector")
                    has_preorder_button = False
                    if preorder_selector:
                        try:
                            preorder_elem = product_elem.locator(preorder_selector)
                            has_preorder_button = (await preorder_elem.count()) > 0
                        except Exception:
                            has_preorder_button = False

                    if has_preorder_button:
                        in_stock = True
                        preorder = True
                    else:
                        preorder = False
                        is_not_released = False
                        if site.get("check_product_page_if_not_released", False):
                            try:
                                not_released_elem = product_elem.locator(site["not_released_selector"])
                                is_not_released = (await not_released_elem.count()) > 0
                            except Exception:
                                is_not_released = False

                        if is_not_released:
                            product_link = None
                            try:
                                product_link_tmp = await product_elem.locator(site["product_link_selector"]).get_attribute("href")
                                if product_link_tmp and product_link_tmp.startswith("/"):
                                    base_url_match = re.match(r"(https?://[^/]+)", url)
                                    base_url = base_url_match.group(1) if base_url_match else ""
                                    product_link = base_url + product_link_tmp
                                else:
                                    product_link = product_link_tmp
                            except Exception:
                                pass

                            if product_link:
                                product_link = clean_product_link(product_link)
                                in_stock = await check_if_preorderable(product_link, preorder_page, site)
                            else:
                                in_stock = False
                        else:
                            in_stock = (availability_status == "i lager")

                    was_available = product_hash in available_products

                    if in_stock and not was_available:
                        status_msg = "Förbeställningsbar" if preorder else "Tillbaka i lager"
                        print(f"  Produkten är {status_msg.lower()}!", flush=True)
                        print(f"[DEBUG] Skickar hash {product_hash} för produkt '{name}' från '{site.get('name', '')}'", flush=True)

                        available_products[product_hash] = name
                        seen_products[product_hash] = name
                        new_available = True
                        await send_discord_message(
                            name=name,
                            url=product_link or url,
                            price=price,
                            status=status_msg,
                            site_name=normalize(site.get("name", url))
                        )

                        if GOOGLE_SHEETS_CREDS and GOOGLE_SHEETS_ID:
                            product_data = {
                                'hash': product_hash,
                                'product_name': name,
                                'price': price,
                                'url': product_link or url,
                                'store': site.get("name", url),
                                'status': status_msg
                            }
                            products_to_update.append(product_data)

                    elif not in_stock and was_available:
                        # Only remove if availability_status == "slutsåld"
                        if availability_status == "slutsåld":
                            print(f"  Produkten finns inte längre i lager, tas bort.", flush=True)
                            del available_products[product_hash]
                        else:
                            print(f"  Produkten saknas på sidan men är inte markerad som slutsåld, tas INTE bort.", flush=True)

                except Exception as e:
                    print(f"Fel vid hantering av produkt {i} på {url}: {e}", flush=True)
                finally:
                    print(f"  Hantering av produkt {i+1} klar på {time.time()-product_start:.2f} sek", flush=True)

        if GOOGLE_SHEETS_CREDS and GOOGLE_SHEETS_ID and products_to_update:
            google_sheets.update_or_append_rows(products_to_update)

        await preorder_page.close()
        await main_page.close()
        await browser.close()

    return new_seen or new_available, all_products_hashes

async def get_all_products(site):
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]

    products_list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                  "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43")

        url = site.get("url") or (site.get("url_pattern").format(page=site.get("start_page", 1)) if "url_pattern" in site else None)
        if not url:
            print("Ingen giltig URL att hämta produkter ifrån.", flush=True)
            await browser.close()
            return []

        try:
            await page.goto(url, timeout=10000)
            await scroll_to_load_all(page, product_selector, site.get("use_mouse_wheel", False))
            products = page.locator(product_selector)
            count = await products.count()
        except Exception as e:
            print(f"Fel vid hämtning av produkter: {e}", flush=True)
            await browser.close()
            return []

        for i in range(count):
            try:
                product_elem = products.nth(i)
                name = normalize(await product_elem.locator(name_selector).text_content(timeout=2000))

                availability_text = ""
                try:
                    elem = product_elem.locator(availability_selector).first
                    span = elem.locator("span")
                    if await span.count() > 0:
                        availability_text = (await span.first.inner_text()).strip()
                    else:
                        availability_text = (await elem.inner_text()).strip()
                except Exception:
                    pass

                status = get_availability_status(product_elem, site)

                if status == "okänd":
                    print(f"🔍 Produkt {i}: '{name}' — Status: {status} — Text: '{availability_text}'", flush=True)

                products_list.append({
                    "name": name,
                    "availability_text": availability_text,
                    "status": status
                })
            except Exception as e:
                print(f"Fel vid hämtning av produkt {i}: {e}", flush=True)

        await browser.close()

    return products_list

async def main():
    seen_products = load_json(SEEN_PRODUCTS_FILE)
    available_products = load_json(AVAILABLE_PRODUCTS_FILE)
    sites = google_sheets.read_sites_from_sheet()

    if not sites:
        print("Inga sites hittades i Google Sheets eller arket är tomt.", flush=True)
        return

    any_changes = False
    all_found_hashes = set()

    # Kör alla scrape_site parallellt för snabbare scanning
    tasks = [scrape_site(site, seen_products, available_products) for site in sites]
    results = await asyncio.gather(*tasks)

    for changed, hashes_this_site in results:
        all_found_hashes.update(hashes_this_site)
        any_changes = any_changes or changed

    # Ta bort produkter som inte längre hittas på någon site
    for old_hash in list(available_products.keys()):
        if old_hash not in all_found_hashes:
            print(f"Produkten med hash {old_hash} hittades inte längre på någon site — tas bort.", flush=True)
            del available_products[old_hash]

    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)
    google_sheets.delete_rows_with_missing_hashes(available_products)

    if sites:
        print("\n--- Alla produkter på första siten ---", flush=True)
        products = await get_all_products(sites[0])
        for prod in products:
            print(f"Namn: {prod['name']} | Tillgänglighet: {prod['availability_text']}", flush=True)

    if not any_changes:
        print("Inga nya eller återkommande produkter upptäcktes.", flush=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"🚨 Fel i main(): {e}", flush=True)

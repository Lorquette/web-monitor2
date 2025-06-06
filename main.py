import asyncio
import aiohttp
import os
import json
import hashlib
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import re
import time
import requests
from urllib.parse import urlparse, urlunparse
import google_sheets
from api_scraper import get_api_products  # <-- new import!

# --- Config and environment variables ---
DATA_DIR = "data"
SEEN_PRODUCTS_FILE = os.path.join(DATA_DIR, "seen_products.json")
AVAILABLE_PRODUCTS_FILE = os.path.join(DATA_DIR, "available_products.json")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_SHEETS_ID_S = os.getenv("GOOGLE_SHEETS_ID_S")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.43"
)

PARALLEL_URLS_PER_SITE = 5
GLOBAL_SCRIPT_TIMEOUT = 1800
SITE_TIMEOUT = 600

KEYWORDS = [
    "Pokémon", "Pokemon", "Destined Rivals", "Prismatic Evolutions",
    "Journey Together", "Black Bolt", "White Flare"
]
BLOCKED_KEYWORDS = [
    "Deck Box", "Binder", "Pärm", "Portfolio", "Playmat",
    "Stacking Tin", "Mugg", "Ryggsäck", "Stort kort", "Ultra Pro"
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
    if text is None:
        return ""
    return ' '.join(text.lower().strip().split())

def capitalize_first(text):
    if not text:
        return ""
    return text[0].upper() + text[1:]

def title_case(text):
    if not text:
        return ""
    s = text.title()
    s = re.sub(r"(?<=\w)'S\b", "'s", s)  # Collector'S -> Collector's
    return s

def generate_product_hash(name, site_name):
    return hash_string(f"{normalize(site_name)}|{normalize(name)}")

def clean_product_link(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

def safe_int(value, default=1):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default

async def send_discord_message(name, url, price, status, site_name):
    if not DISCORD_WEBHOOK:
        print("No Discord webhook set in environment variable.", flush=True)
        return

    # Defensive: ensure no required field is empty
    if not name or not url or not status:
        print(f"[DISCORD] Skipping message due to missing required field: name={name}, url={url}, status={status}")
        return

    price_str = str(price) if price else "Okänt"
    color_map = {
        "Ny produkt": 0xFFFF00,
        "Tillbaka i lager": 0x00FF00,
        "Förbeställningsbar": 0x1E90FF
    }
    formatted_name = name.title()
    formatted_site = capitalize_first(site_name) if site_name else "Okänd butik"
    embed = {
        "title": formatted_name,
        "url": url,
        "color": color_map.get(status, 0x000000),
        "fields": [
            {"name": "Pris", "value": price_str, "inline": True},
            {"name": "Status", "value": status, "inline": True},
            {"name": "Webbplats", "value": formatted_site, "inline": False},
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
            return False
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
    if site.get("availability_status") is True:
        return "i lager"
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
                if any(word in text for word in [
                    "i lager", "available", "in stock", "köp", "boka", "lägg i varukorg",
                    "preorder", "add to cart", "i lager."
                ]):
                    return "i lager"
        except Exception as e:
            print(f"  Fel vid in_stock_selector: {e}", flush=True)
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
                if any(ot in text for ot in out_of_stock_texts if ot):
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
    max_attempts = 6  # <-- was 3, increase for more robustness
    attempts = 0
    max_duration = 12  # <-- was 4, increase for more robustness
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
        await asyncio.sleep(1.2)  # <-- was 0.5, give more time for loading
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

async def scrape_url(url, site, semaphore):
    products_out = []
    product_selector = site["product_selector"]
    name_selector = site["name_selector"]
    base_url = site.get("base_url", "")
    async with semaphore:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                main_page = await browser.new_page(user_agent=USER_AGENT)
                preorder_page = await browser.new_page(user_agent=USER_AGENT)
                await main_page.goto(url, timeout=10000)
                await scroll_to_load_all(main_page, product_selector, site.get("use_mouse_wheel", False))
                products = main_page.locator(product_selector)
                count = await products.count()
                for i in range(count):
                    try:
                        product_elem = products.nth(i)
                        name = normalize(await product_elem.locator(name_selector).text_content(timeout=3500))
                        if not site.get("skip_keywords", False) and not product_matches_keywords(name):
                            continue
                        price = None
                        price_selector = site.get("price_selector")
                        if price_selector:
                            try:
                                price = (await product_elem.locator(price_selector).text_content(timeout=3500)).strip()
                            except Exception:
                                price = None
                        if not price:
                            price = "Okänt"
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
                        product_hash = generate_product_hash(name, site.get("name", ""))
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
                                tmp_link = None
                                try:
                                    tmp_link = await product_elem.locator(site["product_link_selector"]).get_attribute("href")
                                    if tmp_link and tmp_link.startswith("/"):
                                        base_url_match = re.match(r"(https?://[^/]+)", url)
                                        base_url2 = base_url_match.group(1) if base_url_match else ""
                                        tmp_link = base_url2 + tmp_link
                                except Exception:
                                    pass
                                if tmp_link:
                                    clean_link = clean_product_link(tmp_link)
                                    in_stock = await check_if_preorderable(clean_link, preorder_page, site)
                                else:
                                    in_stock = False
                            else:
                                in_stock = (availability_status == "i lager")
                        if in_stock:
                            status = "Tillbaka i lager"
                        elif preorder:
                            status = "Förbeställningsbar"
                        else:
                            status = availability_status
                        
                        products_out.append({
                            "hash": product_hash,
                            "name": name,
                            "url": product_link or url,
                            "price": price,
                            "status": status,
                            "site_name": normalize(site.get("name", url))
                        })
                    except Exception as e:
                        print(f"Fel vid hantering av produkt {i} på {url}: {e}", flush=True)
                await preorder_page.close()
                await main_page.close()
                await browser.close()
        except PlaywrightTimeoutError:
            print(f"[TIMEOUT] Playwright timed out for URL: {url}", flush=True)
        except Exception as e:
            print(f"Exception in scrape_url({url}): {e}", flush=True)
    return products_out

async def scrape_site(site):
    if site.get("type", "browser").lower() == "api":
        # Use API-based scraping
        return get_api_products(site)
    # Browser-based scraping as before
    urls = get_urls_to_scrape(site)
    semaphore = asyncio.Semaphore(site.get("max_parallel_urls", PARALLEL_URLS_PER_SITE))
    url_tasks = [
        asyncio.create_task(asyncio.wait_for(scrape_url(url, site, semaphore), timeout=SITE_TIMEOUT))
        for url in urls
    ]
    products = []
    for task in asyncio.as_completed(url_tasks):
        try:
            result = await task
            products.extend(result)
        except asyncio.TimeoutError:
            print("[TIMEOUT] A single URL scrape timed out.", flush=True)
    return products

async def main():
    sites = google_sheets.read_sites_from_sheet()  # Uses GOOGLE_SHEETS_ID_S for config
    seen_products = load_json(SEEN_PRODUCTS_FILE)
    available_products = load_json(AVAILABLE_PRODUCTS_FILE)
    if not sites:
        print("Inga sites hittades i Google Sheets eller arket är tomt.", flush=True)
        return
    site_tasks = [
        asyncio.create_task(asyncio.wait_for(scrape_site(site), timeout=SITE_TIMEOUT))
        for site in sites
    ]
    all_site_products = []
    try:
        for task in asyncio.as_completed(site_tasks):
            try:
                result = await task
                all_site_products.append(result)
            except asyncio.TimeoutError:
                print("[TIMEOUT] A site scrape timed out.", flush=True)
    except Exception as e:
        print(f"Exception during global site scraping: {e}", flush=True)
    found_products = {}
    for site_products in all_site_products:
        for prod in site_products:
            found_products[prod["hash"]] = prod  # last one wins
    notifications_to_send = []
    products_to_update_google = []
    for prod_hash, prod in found_products.items():
        if prod_hash not in seen_products:
            notifications_to_send.append({
                "name": prod["name"],
                "url": prod["url"],
                "price": prod["price"],
                "status": "Ny produkt",
                "site_name": prod["site_name"]
            })
            seen_products[prod_hash] = prod["name"]
        elif prod["status"] in ("i lager", "förbeställningsbar", "Tillbaka i lager", "Förbeställningsbar") and prod_hash not in available_products:
            notifications_to_send.append({
                "name": prod["name"],
                "url": prod["url"],
                "price": prod["price"],
                "status": "Tillbaka i lager" if prod["status"] == "i lager" else "Förbeställningsbar",
                "site_name": prod["site_name"]
            })
            available_products[prod_hash] = prod["name"]
        if GOOGLE_SHEETS_CREDS and GOOGLE_SHEETS_ID:
            products_to_update_google.append({
                'hash': prod_hash,
                'product_name': title_case(prod["name"]),
                'price': prod["price"],
                'url': prod["url"],
                'store': capitalize_first(prod["site_name"]),
                'status': prod["status"]
            })
    hashes_now = set(found_products.keys())
    for old_hash in list(available_products.keys()):
        if old_hash not in hashes_now:
            print(f"Produkten med hash {old_hash} hittades inte längre på någon site — tas bort.", flush=True)
            del available_products[old_hash]
    for notif in notifications_to_send:
        await send_discord_message(
            notif["name"], notif["url"], notif["price"], notif["status"], notif["site_name"]
        )
        await asyncio.sleep(1.5)
    save_json(SEEN_PRODUCTS_FILE, seen_products)
    save_json(AVAILABLE_PRODUCTS_FILE, available_products)
    if GOOGLE_SHEETS_CREDS and GOOGLE_SHEETS_ID and products_to_update_google:
        google_sheets.deduplicate_sheet_hashes()
        google_sheets.update_or_append_rows(products_to_update_google)
        google_sheets.delete_rows_with_missing_hashes(available_products)
    print("\n--- Alla produkter på första siten ---", flush=True)
    if all_site_products and len(all_site_products[0]) > 0:
        for prod in all_site_products[0]:
            print(f"Namn: {prod['name']} | Tillgänglighet: {prod['status']}", flush=True)
    if not notifications_to_send:
        print("Inga nya eller återkommande produkter upptäcktes.", flush=True)

if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=GLOBAL_SCRIPT_TIMEOUT))
    except asyncio.TimeoutError:
        print(f"\n[GLOBAL TIMEOUT] Script exceeded {GLOBAL_SCRIPT_TIMEOUT} seconds and was terminated.", flush=True)
    except Exception as e:
        print(f"🚨 Fel i main(): {e}", flush=True)

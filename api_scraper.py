import requests
import hashlib
from urllib.parse import urljoin

def hash_product(prod, keys):
    h = hashlib.sha256()
    if "api_id_key" in keys and keys["api_id_key"]:
        h.update(str(deep_get(prod, keys["api_id_key"])).encode())
    if "api_title_key" in keys and keys["api_title_key"]:
        h.update(str(deep_get(prod, keys["api_title_key"])).encode())
    if "api_stock_key" in keys and keys["api_stock_key"]:
        h.update(str(deep_get(prod, keys["api_stock_key"])).encode())
    if "api_preorder_key" in keys and keys["api_preorder_key"]:
        h.update(str(deep_get(prod, keys["api_preorder_key"])).encode())
    return h.hexdigest()

def deep_get(data, key_path):
    if not key_path:
        return None
    keys = key_path.split(".")
    val = data
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return None
    return val

def get_api_products(site_conf):
    api_url = site_conf.get("api_url")
    max_pages = int(site_conf.get("max_pages", 1))
    title_key = site_conf.get("api_title_key", "mainTitle")
    url_key = site_conf.get("api_url_key", "url")
    price_key = site_conf.get("api_price_key", "price")
    stock_key = site_conf.get("api_stock_key", "stock.web")
    preorder_key = site_conf.get("api_preorder_key", "isPreOrderable")
    id_key = site_conf.get("api_id_key", "id")
    site_name = site_conf.get("name", "Webhallen")
    api_items_key = site_conf.get("api_items_key", "products")
    base_url = site_conf.get("api_base_url", "https://www.webhallen.com/")

    products = []
    for page in range(1, max_pages+1):
        url = api_url.format(page=page)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            product_list = data.get(api_items_key, [])
            for prod in product_list:
                name = deep_get(prod, title_key) or ""
                prod_url = deep_get(prod, url_key) or ""
                if not prod_url.startswith("http"):
                    prod_url = urljoin(base_url, prod_url)
                price = deep_get(prod, price_key)
                if isinstance(price, dict):
                    price = price.get("price", None)
                if isinstance(price, (int, float)):
                    price = str(int(price))
                if price is None:
                    price = prod.get("priceText", "okänt")
                stock = deep_get(prod, stock_key)
                preorderable = deep_get(prod, preorder_key)
                status = "slutsåld"
                if stock and str(stock).isdigit() and int(stock) > 0:
                    status = "i lager"
                elif preorderable:
                    status = "förbeställningsbar"
                prod_hash = hash_product(prod, {
                    "api_id_key": id_key,
                    "api_title_key": title_key,
                    "api_stock_key": stock_key,
                    "api_preorder_key": preorder_key,
                })
                products.append({
                    "hash": prod_hash,
                    "name": name,
                    "url": prod_url,
                    "price": price,
                    "status": status,
                    "site_name": site_name
                })
        except Exception as e:
            print(f"[API ERROR] Failed to fetch {url}: {e}")
    valid_products = []
    for p in products:
        if not p["name"] or not p["url"] or not p["status"]:
            print(f"[API WARNING] Skipping product with missing field: {p}")
            continue
        if not p["price"]:
            p["price"] = "Okänt"
        if not p["site_name"]:
            p["site_name"] = site_name
        valid_products.append(p)
    return valid_products

import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import ast
import gspread

# Läs in JSON-credentials från secret
SERVICE_ACCOUNT_INFO = os.getenv("GOOGLE_SHEETS_CREDS")
if not SERVICE_ACCOUNT_INFO:
    raise Exception("Miljövariabeln GOOGLE_SHEETS_CREDS är inte satt")

creds_dict = json.loads(SERVICE_ACCOUNT_INFO)
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

service = build('sheets', 'v4', credentials=creds)

# Läs in spreadsheet-ID från secret (miljövariabel)
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
if not SPREADSHEET_ID:
    raise Exception("Miljövariabeln GOOGLE_SHEETS_ID är inte satt")

SHEET_NAME = 'Blad1'  # Ändra till ditt ark-namn om det behövs


def get_all_hashes():
    """
    Läser in alla hashar från kolumn G i Google Sheets och returnerar som lista.
    """
    sheet = service.spreadsheets()
    range_ = f'{SHEET_NAME}!G2:G'  # Anta att första raden är header, börja på rad 2
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=range_).execute()
    values = result.get('values', [])
    hashes = [row[0] for row in values if row]  # Säkerställ att raden inte är tom
    return hashes

def get_all_hashes_with_row_indices():
    """
    Returns a list of (row_index, hash) for all hashes in the sheet.
    """
    sheet = service.spreadsheets()
    range_ = f'{SHEET_NAME}!G2:G'
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=range_).execute()
    values = result.get('values', [])
    # row_index is 1-based, so +2
    return [(i + 2, row[0]) for i, row in enumerate(values) if row]

def deduplicate_sheet_hashes():
    """
    Removes duplicate rows for the same hash in Google Sheets, keeping only the first occurrence.
    """
    all_hashes = {}
    duplicates = []
    for row_index, hash_val in get_all_hashes_with_row_indices():
        if hash_val in all_hashes:
            duplicates.append(row_index)
        else:
            all_hashes[hash_val] = row_index
    if not duplicates:
        print("[INFO] No duplicate hashes found in Google Sheets.")
        return
    print(f"[INFO] Removing {len(duplicates)} duplicate rows from Google Sheets: {duplicates}")
    # Sort in reverse so row numbers don't shift
    duplicates.sort(reverse=True)
    for row_index in duplicates:
        request_body = {
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": 0,  # adjust if needed!
                        "dimension": "ROWS",
                        "startIndex": row_index - 1,
                        "endIndex": row_index
                    }
                }
            }]
        }
        service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=request_body).execute()
        time.sleep(1)  # To avoid quota

def update_row(row_index, row_data):
    """
    Uppdatera en specifik rad (1-baserad index i Google Sheets) med row_data (lista med värden).
    """
    sheet = service.spreadsheets()
    # Formatera range t.ex. Sheet1!A5 om row_index=5
    range_ = f'{SHEET_NAME}!A{row_index}'
    body = {'values': [row_data]}
    request = sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_,
        valueInputOption='RAW',
        body=body
    )
    response = request.execute()
    return response


def append_row(row_data):
    """
    Lägg till en rad längst ner i Google Sheet utan att skriva över befintliga rader.
    Räknar ut nästa lediga rad genom att läsa antal fyllda rader i kolumn A.
    """
    sheet = service.spreadsheets()

    # Läs in alla värden i kolumn A (från rad 1 och neråt)
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=f'{SHEET_NAME}!A:A').execute()
    values = result.get('values', [])

    # Nästa rad är antal rader med innehåll + 1 (eftersom Sheets är 1-baserat)
    next_row = len(values) + 1

    # Skriv till denna rad
    range_ = f'{SHEET_NAME}!A{next_row}'
    body = {'values': [row_data]}

    request = sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_,
        valueInputOption='RAW',
        body=body
    )
    response = request.execute()
    return response

def update_or_append_rows(products_data):
    """
    Batch-uppdatera eller lägg till flera produkter i Google Sheets.
    products_data: lista av dict med produkternas data, varje dict måste ha 'hash' och övriga fält.
    """
    required_fields = ['product_name', 'price', 'url', 'store', 'hash']
    valid_products = []

    for product_data in products_data:
        for field in required_fields:
            if not product_data.get(field):
                print(f"[!] Fält saknas i produktdata: {field}. Skipping produkt med hash {product_data.get('hash')}.")
                break
        else:
            valid_products.append(product_data)

    if not valid_products:
        print(f"[ERROR] No valid products to update or append!")
        return

    hashes = get_all_hashes()
    sheet = service.spreadsheets()
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    updates = []
    appends = []

    for product_data in valid_products:
        product_hash = product_data['hash']
        row_data = [
            product_data['product_name'],
            product_data['price'],
            product_data['store'],
            product_data.get('status', ''),
            product_data['url'],
            now_str,
            product_hash,
        ]
        if product_hash in hashes:
            row_index = hashes.index(product_hash) + 2  # +2 pga header + 1-baserat index
            updates.append((row_index, row_data))
        else:
            appends.append(row_data)

    # Gör batch-uppdateringar för befintliga rader
    if updates:
        data = []
        for row_index, row_data in updates:
            data.append({
                'range': f'{SHEET_NAME}!A{row_index}',
                'values': [row_data]
            })
        body = {
            'valueInputOption': 'RAW',
            'data': data
        }
        response = sheet.values().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        print(f"[INFO] Uppdaterade {len(updates)} rader i Google Sheets.")

    # Lägg till nya rader i slutet
    if appends:
        response = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{SHEET_NAME}!A:A',
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': appends}
        ).execute()
        print(f"[INFO] La till {len(appends)} nya rader i Google Sheets.")

def delete_rows_with_missing_hashes(available_products):
    """
    Tar bort rader i Google Sheets där hash i kolumn G finns i Sheets men inte i available_products.
    available_products är en dict med hashar som nycklar (från available_products.json).
    """
    sheet = service.spreadsheets()

    # Läs in hela kolumn G, men också radnummer för att kunna ta bort rätt rad
    range_ = f'{SHEET_NAME}!G2:G'
    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=range_).execute()
    values = result.get('values', [])

    # Vi behöver en lista med (row_index, hash)
    # row_index är 1-baserat i Google Sheets, +1 för header och +1 för offset (börjar på rad 2)
    rows_with_hash = [(i + 2, row[0]) for i, row in enumerate(values) if row]

    # Identifiera rader att ta bort
    rows_to_delete = [row_index for row_index, hash_val in rows_with_hash if hash_val not in available_products]

    if not rows_to_delete:
        print("[INFO] Inga rader att ta bort från Google Sheets.")
        return

    print(f"[INFO] Kommer ta bort {len(rows_to_delete)} rader från Google Sheets: {rows_to_delete}")

    # Viktigt: Radera från botten till toppen så att radnumren inte skiftar när vi tar bort flera
    rows_to_delete.sort(reverse=True)

    for row_index in rows_to_delete:
        request_body = {
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": 0,  # OBS: Kolla sheetId! Om du inte vet, kan du behöva hämta det från Sheets API
                        "dimension": "ROWS",
                        "startIndex": row_index - 1,  # 0-baserat index i API
                        "endIndex": row_index
                    }
                }
            }]
        }
        response = service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=request_body).execute()
        print(f"[INFO] Tog bort rad {row_index} i Google Sheets.")


def convert_value(val):
    """Försök konvertera värdet till rätt typ."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    if not isinstance(val, str):
        return val
    val = val.strip()
    if val.lower() in ["true", "yes", "1"]:
        return True
    if val.lower() in ["false", "no", "0"]:
        return False
    if val.startswith("[") and val.endswith("]"):
        try:
            return ast.literal_eval(val)
        except:
            return val
    return val


def read_sites_from_sheet():
    SERVICE_ACCOUNT_INFO = os.getenv("GOOGLE_SHEETS_CREDS")
    if not SERVICE_ACCOUNT_INFO:
        raise Exception("Miljövariabeln GOOGLE_SHEETS_CREDS är inte satt")

    creds_dict = json.loads(SERVICE_ACCOUNT_INFO)
    gc = gspread.service_account_from_dict(creds_dict)

    SPREADSHEET_ID_S = os.getenv("GOOGLE_SHEETS_ID_S")
    if not SPREADSHEET_ID_S:
        raise Exception("Miljövariabeln GOOGLE_SHEETS_ID_S är inte satt")

    sh = gc.open_by_key(SPREADSHEET_ID_S)
    worksheet = sh.worksheet("Sites")

    data = worksheet.get("A1:H40") #Tillfälligt begränsad medan felsökning pågår

    # Första raden är nycklar (kolumnrubriker) - första kolumn är 'key'
    keys = data[0]
    rows = data[1:]

    sites = []
    for col_idx in range(1, len(keys)):
        site = {}
        for row_idx in range(len(rows)):
            key = rows[row_idx][0].strip()
            if not key:
                continue
            value = rows[row_idx][col_idx] if col_idx < len(rows[row_idx]) else ''
            if value:
                site[key] = convert_value(value)
        sites.append(site)

    # Debug-utskrift av all data
    print("DEBUG: Lästa sites från Google Sheets:")
    print(json.dumps(sites, indent=2, ensure_ascii=False))

    # Validera att viktiga nycklar finns i varje site
    viktiga_nycklar = ['name', 'product_selector']
    for i, site in enumerate(sites):
        if site.get("type", "browser") == "api":
            # For API sites, only check for 'name'
            if "name" not in site:
                print(f"DEBUG WARNING: API site index {i} saknar namn.")
        else:
            saknas = [k for k in viktiga_nycklar if k not in site]
            if saknas:
                print(f"DEBUG WARNING: Site index {i} saknar nycklar: {saknas}")

    return sites

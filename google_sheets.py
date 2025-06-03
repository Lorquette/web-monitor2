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

def update_or_append_row(product_data):
    """
    Uppdaterar en rad med hash i Google Sheets om den finns,
    annars lägger till en ny rad.
    """
    required_fields = ['product_name', 'price', 'url', 'store']
    for field in required_fields:
        if not product_data.get(field):
            print(f"[!] Fält saknas i produktdata: {field}. Skipping Sheets update.")
            return

    hashes = get_all_hashes()

    product_hash = product_data.get('hash')
    if not product_hash:
        raise ValueError("product_data måste innehålla 'hash'")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

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
        row_index = hashes.index(product_hash) + 2
        return update_row(row_index, row_data)
    else:
        return append_row(row_data)

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

    data = worksheet.get("A1:G29")

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
        saknas = [k for k in viktiga_nycklar if k not in site]
        if saknas:
            print(f"DEBUG WARNING: Site index {i} saknar nycklar: {saknas}")

    return sites


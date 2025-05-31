import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime


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

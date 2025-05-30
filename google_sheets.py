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
    Lägg till en rad (lista med värden) längst ner i Google Sheet
    """
    sheet = service.spreadsheets()
    request = sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f'{SHEET_NAME}!A1',
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': [row_data]}
    )
    response = request.execute()
    return response

def update_or_append_row(product_data):
    """
    Uppdaterar en rad med hash i Google Sheets om den finns,
    annars lägger till en ny rad.
    product_data är en dict med alla fält, men vi mappar till en lista med kolumner.
    """
    # 1. Hämta alla hashar i Sheets
    hashes = get_all_hashes()

    product_hash = product_data.get('hash')
    if not product_hash:
        raise ValueError("product_data måste innehålla 'hash'")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    # Mappa product_data till rad som lista med rätt ordning i Sheets
    # EXEMPEL: (du behöver justera efter din kolumnordning)
    row_data = [
        product_data.get('product_name', ''),
        product_data.get('price', ''),
        product_data.get('store', ''),
        product_data.get('status', ''),
        product_data.get('url', ''),
        now_str,
        product_hash,
        # Lägg till fler fält efter behov
    ]

    if product_hash in hashes:
        # Uppdatera raden (lägg till 2 för att hoppa över header och pga 1-baserad index)
        row_index = hashes.index(product_hash) + 2
        return update_row(row_index, row_data)
    else:
        # Append ny rad
        return append_row(row_data)

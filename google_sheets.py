import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

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

SHEET_NAME = 'Sheet1'  # Ändra till ditt ark-namn om det behövs

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

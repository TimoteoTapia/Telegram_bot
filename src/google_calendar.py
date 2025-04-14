# google_calendar.py
import os
import datetime
import json
from pathlib import Path
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# SERVICE_ACCOUNT_INFO = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
# Obtener la ruta del archivo JSON desde .env
# json_path = Path(__file__).parent.parent / os.getenv(
#     "GOOGLE_SERVICE_ACCOUNT_JSON"
# ).strip('"')

# # Cargar el contenido del archivo
# with open(json_path, "r") as f:
#     SERVICE_ACCOUNT_INFO = json.load(f)

# Obtener el valor de la variable de entorno
service_account_json_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# Estrategia mejorada para determinar cómo procesar la variable
SERVICE_ACCOUNT_INFO = None

# Si el valor comienza con '{', probablemente es un JSON directo
if service_account_json_value and service_account_json_value.strip().startswith("{"):
    try:
        SERVICE_ACCOUNT_INFO = json.loads(service_account_json_value)
    except json.JSONDecodeError as e:
        print(f"Error al parsear JSON: {e}")
        raise
else:
    # Es probablemente una ruta de archivo
    try:
        json_path = Path(__file__).parent.parent / service_account_json_value.strip('"')
        if json_path.exists():
            with open(json_path, "r") as f:
                SERVICE_ACCOUNT_INFO = json.load(f)
        else:
            raise FileNotFoundError(f"No se encontró el archivo JSON en: {json_path}")
    except Exception as e:
        print(f"Error al cargar archivo JSON: {e}")
        raise

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

credentials = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO, scopes=SCOPES
)
service = build("calendar", "v3", credentials=credentials)


def create_event(summary, description, start_time, duration_minutes=30):
    end_time = start_time + datetime.timedelta(minutes=duration_minutes)
    event = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "America/Mexico_City",
        },
        "end": {"dateTime": end_time.isoformat(), "timeZone": "America/Mexico_City"},
    }

    created_event = (
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    )
    return created_event["id"]


def delete_event(event_id):
    service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()

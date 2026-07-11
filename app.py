"""
VascuScribe - Vascular Surgery Note Assistant
Backend API v2.3 - Polar Sandbox Fix Edition
============================================
"""

import hashlib
import json
import os
import time
import traceback
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

# ============ JWT EKLENTILERI ============
from jose import jwt, JWTError
import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ============================================================
# AYARLAR
# ============================================================
import pathlib

env_path = pathlib.Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line_env in f:
            line_env = line_env.strip()
            if '=' in line_env and not line_env.startswith('#'):
                key, value = line_env.split('=', 1)
                os.environ[key] = value

app = FastAPI(title="VascuScribe API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_KEY:
    print("UYARI: OPENAI_API_KEY ayarlanmamis!")

client = OpenAI(api_key=OPENAI_KEY)

# ============ JWT AYARLARI ============
JWT_SECRET = os.getenv("JWT_SECRET", "vascuscribe-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=JWT_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(token: str = Header(None, alias="Authorization")):
    if not token:
        raise HTTPException(status_code=401, detail="Token gerekli")
    if token.startswith("Bearer "):
        token = token[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Gecersiz token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Gecersiz veya suresi dolmus token")

# ============================================================
# POLAR ENTEGRASYONU - ROBUST VERSION
# ============================================================
try:
    from polar_sdk import Polar
    from polar_sdk.webhooks import validate_event, WebhookVerificationError
    POLAR_AVAILABLE = True
    print("✅ Polar SDK yuklendi")
except ImportError as e:
    Polar = None
    validate_event = None
    WebhookVerificationError = Exception
    POLAR_AVAILABLE = False
    print(f"⚠️ Polar SDK yuklenemedi: {e}")

POLAR_ACCESS_TOKEN = os.getenv("POLAR_ACCESS_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.getenv("POLAR_WEBHOOK_SECRET", "")

polar_client = None
if POLAR_AVAILABLE and POLAR_ACCESS_TOKEN:
    try:
        polar_client = Polar(
            access_token=POLAR_ACCESS_TOKEN,
            server=os.getenv("POLAR_SERVER", "sandbox")
        )
        print("✅ Polar client basariyla olusturuldu")
    except Exception as e:
        print(f"⚠️ Polar client olusturulamadi: {e}")
        polar_client = None
else:
    print("⚠️ Polar devre disi: TOKEN eksik veya SDK yuklenemedi")

POLAR_PRODUCTS = {
    "mini":     os.getenv("POLAR_MINI_PRICE_ID", ""),
    "standard": os.getenv("POLAR_STANDARD_PRICE_ID", ""),
    "pro":      os.getenv("POLAR_PRO_PRICE_ID", ""),
    "enterprise": os.getenv("POLAR_ENTERPRISE_PRICE_ID", ""),
}

# ============================================================
# KOTA SISTEMI
# ============================================================
users_db = {}

def save_users():
    with open("users.json", "w") as f:
        json.dump({"users": users_db}, f, indent=2)

def load_users():
    global users_db
    try:
        with open("users.json", "r") as f:
            data = json.load(f)
            users_db = data.get("users", {})
    except (FileNotFoundError, json.JSONDecodeError):
        users_db = {}

load_users()

PLANS = {
    "trial":    {"price": 0,     "credits": 1,   "days": 7},
    "mini":     {"price": 990,  "credits": 3,   "days": 30},
    "standard": {"price": 2300, "credits": 10,  "days": 30},
    "pro":      {"price": 4900, "credits": 25,  "days": 30},
    "enterprise": {"price": 19900, "credits": 100, "days": 30},
}

# ============================================================
# KDC SABLONLARI - 11 TEMPLATE (TAMAMI KORUNDU)
# ============================================================

KDC_TEMPLATES = {
    "CABG": {
        "name": "Coronary Artery Bypass Grafting (CABG)",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED. LEFT INTERNAL MAMMARY ARTERY (LIMA) WAS "
            "HARVESTED. THE PATIENT WAS FULLY HEPARINIZED. PERICARDIUM WAS OPENED "
            "AND SUSPENDED. AORTA, AORTIC ROOT AND TWO-STAGE VENOUS CANNULA SUTURES "
            "WERE PLACED. AFTER CANNULATION, TRANSVERSE AND OBLIQUE SINUSES WERE "
            "DISSECTED. THE PATIENT WAS PLACED ON CARDIOPULMONARY BYPASS AND COOLED "
            "TO 32 DEGREES CELSIUS. AORTIC CROSS-CLAMP WAS APPLIED AND ANTEGRADE "
            "BLOOD CARDIOPLEGIA WAS ADMINISTERED FOR MYOCARDIAL PROTECTION AND "
            "DIASTOLIC ARREST. INTERMITTENT ANTEGRADE BLOOD CARDIOPLEGIA WAS "
            "CONTINUED FOR MYOCARDIAL PROTECTION. SUBSEQUENTLY, {graft_details} "
            "AFTER {cross_clamp_time} MINUTES OF AORTIC CROSS-CLAMP TIME, THE CLAMP WAS REMOVED AND "
            "THE HEART RESUMED NORMAL SINUS RHYTHM. AFTER REMOVAL OF THE AORTIC SIDE "
            "CLAMP, THE PATIENT WAS WEANED FROM CPB IN A CONTROLLED MANNER. AFTER "
            "HEMOSTASIS, PROTAMINE WAS ADMINISTERED TO NEUTRALIZE HEPARIN AND "
            "DECANNULATION WAS PERFORMED. AFTER GENERAL HEMOSTASIS, ONE 36F DRAIN "
            "WAS PLACED IN THE MEDIASTINUM AND ONE 32F DRAIN IN THE LEFT HEMITHORAX. "
            "THE STERNUM WAS WIRED AND THE SKIN AND SUBCUTANEOUS TISSUES WERE CLOSED "
            "IN ANATOMICAL LAYERS. TOTAL ASPIRATE: {blood_loss} ML."
        ),
        "keywords": ["bypass", "cabg", "lima", "svg", "graft", "anastomosis",
                     "coronary", "lad", "cx", "rca", "diagonal", "om", "pda", "plb"]
    },
    "KAROTIS": {
        "name": "Carotid Endarterectomy",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, AN "
            "INCISION WAS MADE AT THE RIGHT MASTOID LEVEL TOWARD THE "
            "STERNOCLEIDOMASTOID JUNCTION. THE RIGHT COMMON, EXTERNAL AND INTERNAL "
            "CAROTID ARTERIES WERE DISSECTED INDIVIDUALLY. 7500 UNITS OF HEPARIN "
            "WERE ADMINISTERED AND THE ARTERIES WERE CLAMPED. ARTERIOTOMY WAS "
            "PERFORMED AND EXTENDED TOWARD THE INTERNAL CAROTID. ENDARTERECTOMY WAS "
            "PERFORMED ON THE INTERNAL CAROTID, COMMON CAROTID AND EXTERNAL CAROTID "
            "ARTERIES. THE ARTERIOTOMY WAS CLOSED USING 6/0 PROLENE AND SAPHENOUS "
            "VEIN PATCH IN A PATCHPLASTY FASHION WITH CONTINUOUS TECHNIQUE AND "
            "CIRCULATION WAS RESTORED. OCCLUSION TIME WAS {clamp_time} MINUTES. "
            "SUBSEQUENTLY, MEDIAN STERNOTOMY PHASE WAS INITIATED. THE PLATYSMA OF "
            "THE RIGHT CAROTID ENDARTERECTOMY WAS APPROXIMATED WITH 3/0 VICRYL. SKIN "
            "WAS CLOSED WITH 3/0 VICRYL. TOTAL ASPIRATE: {blood_loss} ML."
        ),
        "keywords": ["karotis", "carotid", "endarterektomi", "endarterectomy", "patch", "patchplasti", "patchplasty", "internal", "external", "common", "occlusion", "clamp"]
    },
    "PSODOANEVRIZMA": {
        "name": "Pseudoaneurysm Repair",
        "template": (
            "UNDER GENERAL ANESTHESIA, IN SUPINE POSITION, AFTER PROPER PREPARATION "
            "AND DRAPING, THE RIGHT INGUINAL REGION WAS OPENED. THE RIGHT COMMON "
            "FEMORAL ARTERY AND THE SPIRAL GRAFT OF THE FEMOROFEMORAL BYPASS WERE "
            "IDENTIFIED AND DISSECTED. 1 ML IV HEPARIN WAS ADMINISTERED. "
            "SUBSEQUENTLY, A VASCULAR CLAMP WAS APPLIED TO THE EXISTING SPIRAL GRAFT "
            "IN THE RIGHT FEMORAL REGION AND THE PSEUDOANEURYSM AREA IN THE LEFT "
            "INGUINAL REGION WAS OPENED IN A CONTROLLED MANNER. IN THE LEFT FEMORAL "
            "REGION, THE GRAFT WAS FOUND TO BE DETACHED FROM THE ANASTOMOSIS SITE. "
            "THE LEFT COMMON-DEEP AND SUPERFICIAL FEMORAL ARTERIES WERE DISSECTED "
            "AND VASCULAR CLAMPS WERE APPLIED. THE SPIRAL GRAFT OF THE EXISTING "
            "FEMOROFEMORAL BYPASS WAS CONSIDERED INFECTED AND DECIDED TO BE REMOVED. "
            "AFTER HEMOSTASIS, THE ARTERIOTOMY AREA ON THE LEFT COMMON FEMORAL "
            "ARTERY WAS PREPARED FOR PATCHPLASTY. SUBSEQUENTLY, APPROPRIATE SIZED "
            "SAPHENOUS VEIN WAS HARVESTED FROM THE RIGHT SUPRAGENICULAR AREA. THE "
            "LEFT COMMON FEMORAL ARTERY ARTERIOTOMY SITE WAS REPAIRED USING 6/0 "
            "PROLENE IN PATCHPLASTY FASHION. ALL VASCULAR CLAMPS WERE REMOVED AND "
            "PULSE WAS PALPABLE OVER AND DISTAL TO THE PATCHPLASTY. AFTER HEMOSTASIS, "
            "HEMOVAC DRAINS WERE PLACED IN ALL INCISION SITES AND LAYERS WERE CLOSED "
            "IN APPOSITION. INTRAOPERATIVE BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["pseudoaneurysm", "femoral", "patchplasty", "spiral", "graft",
                     "inguinal", "femorofemoral"]
    },
    "AVR": {
        "name": "Aortic Valve Replacement (AVR)",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, UPPER "
            "MINI MEDIAN STERNOTOMY WAS PERFORMED. THE PATIENT WAS FULLY HEPARINIZED, "
            "PERICARDIUM WAS OPENED AND SUSPENDED. AORTA, AORTIC ROOT, TWO-STAGE "
            "VENOUS CANNULA SUTURES WERE PLACED. AFTER CANNULATION, TOTAL "
            "CARDIOPULMONARY BYPASS WAS INITIATED AND THE PATIENT WAS COOLED TO 32 "
            "DEGREES CELSIUS. AORTIC CROSS-CLAMP WAS APPLIED AND AORTOTOMY WAS "
            "PERFORMED. SELECTIVE BLOOD CARDIOPLEGIA WAS ADMINISTERED THROUGH "
            "CORONARY OSTIA FOR MYOCARDIAL PROTECTION AND DIASTOLIC ARREST. NATIVE "
            "AORTIC VALVE WAS EVALUATED. AORTIC VALVE CUSPS WERE EXCISED. USING 2.0 "
            "TICRON SUTURE MATERIALS, A {valve_size} MM MECHANICAL BILEAFLET AORTIC "
            "VALVE WAS IMPLANTED WITH INDIVIDUAL SUTURES. AORTOTOMY WAS CLOSED. "
            "SUBSEQUENTLY, DEAIRING WAS PERFORMED, AORTIC CROSS-CLAMP WAS REMOVED, "
            "AND THE HEART RESUMED SPONTANEOUS NORMAL SINUS RHYTHM. THE PATIENT WAS "
            "WEANED FROM CPB IN A CONTROLLED MANNER. AFTER HEMOSTASIS, PROTAMINE WAS "
            "ADMINISTERED TO NEUTRALIZE HEPARIN AND DECANNULATION WAS PERFORMED. "
            "EPICARDIAL PACEMAKER WIRES WERE PLACED. AFTER GENERAL HEMOSTASIS, TWO "
            "36F DRAINS WERE PLACED IN THE MEDIASTINUM. THE STERNUM WAS WIRED AND "
            "SKIN-SUBCUTANEOUS LAYERS WERE CLOSED IN ANATOMICAL LAYERS. "
            "INTRAOPERATIVE BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["aort", "aortic", "avr", "kapak", "valve", "bileaflet", "mekanik", "mechanical", "aortotomy", "cross-clamp"]
    },
    "CABG_MVR": {
        "name": "CABG + Mitral Valve Replacement (MVR)",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED AND SIMULTANEOUSLY RIGHT SVG GRAFT WAS "
            "PREPARED. AFTER MEDIAN STERNOTOMY, LIMA WAS HARVESTED AND PERICARDIUM "
            "WAS OPENED AND SUSPENDED. THE PATIENT WAS FULLY HEPARINIZED. ROUTINE "
            "AORTA, AORTIC ROOT AND BICAVA VENOUS CANNULATION WAS PERFORMED. RIGHT "
            "PULMONARY VENT AND RETROGRADE CARDIOPLEGIA CANNULAE WERE PLACED IN "
            "APPROPRIATE POSITIONS. THE PATIENT WAS PLACED ON CPB AND COOLED. AORTIC "
            "CROSS-CLAMP WAS APPLIED. ANTEGRADE COLD BLOOD CARDIOPLEGIA WAS "
            "ADMINISTERED AND DIASTOLIC ARREST WAS ACHIEVED. MYOCARDIAL PROTECTION "
            "WAS MAINTAINED WITH CONTINUOUS RETROGRADE AND INTERMITTENT ANTEGRADE "
            "BLOOD CARDIOPLEGIA. DISTAL ANASTOMOSES WERE PERFORMED FIRST. "
            "SUBSEQUENTLY, OBLIQUE LEFT ATRIOTOMY WAS PERFORMED. NATIVE MITRAL "
            "VALVE WAS VISUALIZED, STENOSIS AND CALCIFICATION WERE PRESENT. ANTERIOR "
            "LEAFLET WAS COMPLETELY AND POSTERIOR LEAFLET PARTIALLY EXCISED. A "
            "{valve_size} MM ST. JUDE MECHANICAL MITRAL VALVE WAS IMPLANTED WITH "
            "INDIVIDUAL PLEDGETED SUTURES. AFTER VALVE CHECK, ATRIOTOMY WAS CLOSED "
            "WITH 4/0 PROLENE IN CONTINUOUS FASHION. LIMA-LAD ANASTOMOSIS WAS "
            "PERFORMED. DEAIRING WAS PERFORMED AND AORTIC CROSS-CLAMP WAS REMOVED. "
            "TEMPORARY PACEMAKER WIRES WERE PLACED. INOTROPIC SUPPORT WAS INITIATED. "
            "AFTER HEMOSTASIS, THE PATIENT WAS WEANED FROM CPB IN A CONTROLLED "
            "MANNER. HEPARIN NEUTRALIZATION WAS PERFORMED WITH SIMULTANEOUS "
            "DECANNULATION. ONE 36F AND ONE 32F DRAINS WERE PLACED IN THE MEDIASTINUM. "
            "THE STERNUM WAS WIRED AND SKIN-SUBCUTANEOUS TISSUES WERE CLOSED IN "
            "APPOSITION. THE PATIENT WAS TRANSFERRED TO ICU IN STABLE CONDITION. "
            "TOTAL BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["mvr", "mitral", "cabg", "mechanical", "st jude", "atriotomy",
                     "leaflet", "pledgeted"]
    },
    "MVR": {
        "name": "Mitral Valve Replacement (MVR)",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED. THE PATIENT WAS FULLY HEPARINIZED, PERICARDIUM "
            "WAS OPENED AND SUSPENDED. AORTA, AORTIC ROOT, RETROGRADE BICAVAL VENOUS "
            "CANNULA AND LEFT ATRIAL VENT SUTURES WERE PLACED. AFTER CANNULATION, "
            "TOTAL CARDIOPULMONARY BYPASS WAS INITIATED AND THE PATIENT WAS COOLED TO "
            "{cooling_temp} DEGREES CELSIUS. AORTIC CROSS-CLAMP WAS APPLIED AND "
            "{cardioplegia_type} CARDIOPLEGIA WAS ADMINISTERED FOR MYOCARDIAL PROTECTION "
            "AND DIASTOLIC ARREST. LEFT ATRIUM WAS OPENED. NATIVE MITRAL VALVE WAS "
            "EVALUATED. {leaflet_condition} LEAFLETS WERE EXCISED. USING 2.0 TICRON "
            "SUTURE MATERIALS, A {valve_size} MM MECHANICAL BILEAFLET MITRAL VALVE "
            "WAS IMPLANTED WITH INDIVIDUAL {suture_type} SUTURES. LEFT ATRIOTOMY WAS "
            "CLOSED. DEAIRING WAS PERFORMED, AORTIC CROSS-CLAMP WAS REMOVED, AND THE "
            "HEART RESUMED SPONTANEOUS {rhythm}. THE PATIENT WAS WEANED FROM CPB IN A "
            "CONTROLLED MANNER. AFTER HEMOSTASIS, PROTAMINE WAS ADMINISTERED TO "
            "NEUTRALIZE HEPARIN AND DECANNULATION WAS PERFORMED. {pacemaker} EPICARDIAL "
            "PACEMAKER WIRES WERE PLACED. AFTER GENERAL HEMOSTASIS, {drain_placement} "
            "DRAINS WERE PLACED IN THE MEDIASTINUM. THE STERNUM WAS WIRED AND "
            "SKIN-SUBCUTANEOUS LAYERS WERE CLOSED IN ANATOMICAL LAYERS. INTRAOPERATIVE "
            "BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["mvr", "mitral", "mitral kapak", "bileaflet", "mekanik", "mechanical", "st jude", "atriotomy", "leaflet", "pledgeted"]
    },
    "TRIKUSPIT_RING": {
        "name": "Tricuspid Ring Annuloplasty",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED. THE PATIENT WAS FULLY HEPARINIZED, PERICARDIUM "
            "WAS OPENED AND SUSPENDED. AORTA, AORTIC ROOT, RETROGRADE BICAVAL VENOUS "
            "CANNULA AND LEFT ATRIAL VENT SUTURES WERE PLACED. AFTER CANNULATION, "
            "TOTAL CARDIOPULMONARY BYPASS WAS INITIATED AND THE PATIENT WAS COOLED TO "
            "{cooling_temp} DEGREES CELSIUS. AORTIC CROSS-CLAMP WAS APPLIED AND "
            "{cardioplegia_type} CARDIOPLEGIA WAS ADMINISTERED FOR MYOCARDIAL PROTECTION "
            "AND DIASTOLIC ARREST. RIGHT ATRIUM WAS OPENED. NATIVE TRICUSPID VALVE WAS "
            "EVALUATED. {valve_condition} A {ring_size} MM {ring_brand} TRICUSPID "
            "ANNULOPLASTY RING WAS IMPLANTED WITH INDIVIDUAL {suture_type} SUTURES. "
            "RIGHT ATRIOTOMY WAS CLOSED. DEAIRING WAS PERFORMED, AORTIC CROSS-CLAMP "
            "WAS REMOVED, AND THE HEART RESUMED SPONTANEOUS {rhythm}. THE PATIENT "
            "WAS WEANED FROM CPB IN A CONTROLLED MANNER. AFTER HEMOSTASIS, PROTAMINE "
            "WAS ADMINISTERED TO NEUTRALIZE HEPARIN AND DECANNULATION WAS PERFORMED. "
            "{pacemaker} EPICARDIAL PACEMAKER WIRES WERE PLACED. AFTER GENERAL "
            "HEMOSTASIS, {drain_placement} DRAINS WERE PLACED. THE STERNUM WAS WIRED "
            "AND SKIN-SUBCUTANEOUS LAYERS WERE CLOSED IN ANATOMICAL LAYERS. "
            "INTRAOPERATIVE BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["trikuspid", "tricuspid", "ring", "annuloplasty", "edwards",
                     "mc3", "right atrium", "atriotomy"]
    },
    "VARIS": {
        "name": "Varicose Vein Surgery (VSM Stripping / RF Ablation)",
        "template": (
            "UNDER {anesthesia_type}, AFTER PROPER PREPARATION AND DRAPING, THE "
            "{side} LOWER EXTREMITY WAS STERILELY PREPARED AND DRAPED. THE {side} "
            "INGUINAL REGION WAS INCised AND THE GREAT SAPHENOUS VEIN (VSM) WAS "
            "IDENTIFIED AND DISSECTED. SIDE BRANCHES WERE LIGATED. SUBSEQUENTLY, THE "
            "VSM WAS IDENTIFIED AT THE {ankle_level} LEVEL AND DISSECTED. "
            "{stripping_method} STRIPPING OF THE VSM WAS PERFORMED. "
            "{pack_count} VARICOSE VEIN PACKS WERE EXCISED BY {removal_method} FROM "
            "THE {side} LEG. AFTER HEMOSTASIS, INCISIONS WERE CLOSED IN ANATOMICAL "
            "LAYERS. {compression} WAS APPLIED. INTRAOPERATIVE BLOOD LOSS: "
            "{blood_loss}. NO COMPLICATIONS OCCURRED."
        ),
        "keywords": ["varis", "vsm", "safen", "stripping", "ablasyon", "ablation",
                     "radyofrekans", "rf", "ven", "phlebectomy", "pack"]
    },
    "EMBOLEKTOMI": {
        "name": "Embolectomy / Thrombectomy",
        "template": (
            "UNDER {anesthesia_type}, AFTER PROPER PREPARATION AND DRAPING, THE "
            "{side} INGUINAL REGION WAS RE-OPENED. THE FEMORAL ARTERY, PREVIOUS "
            "ANASTOMOSIS LINE AND GRAFT WERE EXPOSED AND DISSECTED. AFTER 1 ML IV "
            "HEPARIN, GRAFTOTOMY WAS PERFORMED. A {catheter_size} FOGARTY CATHETER "
            "WAS ADVANCED PROXIMALLY. {proximal_result} THROMBUS WAS EXTRACTED. "
            "ANTEGRADE FLOW WAS {flow_quality}. A FOGARTY CATHETER WAS ADVANCED "
            "DISTALLY. {distal_result} RETROGRADE FLOW WAS {retrograde_quality}. "
            "GRAFTOTOMY WAS CLOSED WITH {suture_material}. AFTER HEMOSTASIS, THE "
            "INCISION WAS CLOSED. POSTOPERATIVE PULSES WERE PALPABLE. INTRAOPERATIVE "
            "BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["embolektomi", "embolectomy", "trombektomi", "thrombectomy",
                     "fogarty", "trombus", "thrombus", "graftotomi", "proximal",
                     "distal", "retrograde"]
    },
    "BENTALL": {
        "name": "Bentall De Bono Procedure",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED. THE ASCENDING AORTA, TWO-STAGE VENOUS CANNULA, "
            "ANTEGRADE AND RETROGRADE CARDIOPLEGIA CANNULATIONS WERE PERFORMED. "
            "CARDIOPULMONARY BYPASS WAS INITIATED. AN ASCENDING AORTA ANEURYSM OF "
            "APPROXIMATELY {aneurysm_size} CM IN DIAMETER EXTENDING TO THE PROXIMAL "
            "ARCH WAS IDENTIFIED. CROSS-CLAMP WAS APPLIED. AORTOTOMY WAS PERFORMED, "
            "AND DIASTOLIC ARREST WAS ACHIEVED WITH SELECTIVE ANTEGRADE BLOOD "
            "CARDIOPLEGIA. {vent_description} VENT WAS PERFORMED. THE ANEURYSM WAS "
            "RESECTED. AORTIC VALVE {valve_condition}. THE LEFT AND RIGHT CORONARY "
            "OSTIA WERE PREPARED IN BUTTON FASHION. A {conduit_size} MM ST JUDE "
            "VALVED CONDUIT WAS FIRST IMPLANTED AT THE AORTIC VALVE POSITION WITH "
            "INDIVIDUAL PLEDGETED SUTURES. THE LEFT CORONARY OSTIUM WAS ANASTOMOSED "
            "END-TO-SIDE TO THE CONDUIT USING 5/0 PROLENE IN CONTINUOUS TECHNIQUE. "
            "THE RIGHT CORONARY OSTIUM WAS ANASTOMOSED TO THE DISTAL GRAFT USING "
            "6/0 PROLENE IN END-TO-SIDE AND CONTINUOUS FASHION. AORTIC ROOT "
            "CANNULATION WAS PERFORMED ON THE GRAFT, DEAIRING WAS DONE, AND THE "
            "CROSS-CLAMP WAS REMOVED ({cross_clamp_time} MINUTES). THE HEART RESUMED "
            "NORMAL SINUS RHYTHM. WITH INOTROPIC SUPPORT, THE PATIENT WAS WEANED FROM "
            "CPB ({cpb_time} MINUTES). {drain_count} MEDIASTINAL DRAINS AND "
            "{pleural_drain} PLEURAL DRAIN WERE PLACED. TEMPORARY EPICARDIAL PACEMAKER "
            "WIRES WERE PLACED. THE STERNUM WAS CLOSED WITH STEEL WIRES. SKIN AND "
            "SUBCUTANEOUS TISSUES WERE CLOSED IN APPOSITION. THE PATIENT WAS "
            "TRANSFERRED TO INTENSIVE CARE UNIT. INTRAOPERATIVE BLOOD LOSS: "
            "{blood_loss} ML."
        ),
        "keywords": ["bentall", "de bono", "conduit", "valved conduit", "diseksiyon",
                     "dissection", "anevrizma", "aneurysm", "button", "koroner ostium",
                     "coronary ostia", "aortik kök", "aortic root"]
    },
    "AVR_ASCENDING_AORTA": {
        "name": "AVR + Supracoronary Ascending Aorta Replacement",
        "template": (
            "UNDER GENERAL ANESTHESIA, AFTER PROPER PREPARATION AND DRAPING, MEDIAN "
            "STERNOTOMY WAS PERFORMED. THE PATIENT WAS FULLY HEPARINIZED, PERICARDIUM "
            "WAS OPENED AND SUSPENDED. AORTA, AORTIC ROOT, TWO-STAGE VENOUS CANNULA "
            "AND RETROGRADE SUTURES WERE PLACED. AFTER CANNULATION, TOTAL "
            "CARDIOPULMONARY BYPASS WAS INITIATED AND THE PATIENT WAS COOLED TO "
            "{cooling_temp} DEGREES CELSIUS. ANTEGRADE AND RETROGRADE BLOOD "
            "CARDIOPLEGIA WAS ADMINISTERED FOR MYOCARDIAL PROTECTION AND DIASTOLIC "
            "ARREST. AORTIC CROSS-CLAMP WAS APPLIED AND AORTOTOMY WAS PERFORMED. "
            "NATIVE AORTIC VALVE WAS EVALUATED AND FOUND TO BE {valve_pathology}. "
            "AORTIC VALVE CUSPS WERE EXCISED. USING 2.0 TICRON SUTURE MATERIALS, A "
            "{valve_size} MM MECHANICAL BILEAFLET AORTIC VALVE WAS IMPLANTED WITH "
            "INDIVIDUAL SUTURES. THE ASCENDING AORTA, CORONARY OSTIA AND AORTIC ROOT "
            "WERE EVALUATED. CORONARY REIMPLANTATION WAS NOT REQUIRED, AND "
            "SUPRACORONARY ASCENDING AORTA REPLACEMENT WAS DECIDED. THE NON-CORONARY "
            "SINUS REGION OF THE AORTA WAS RESECTED. A {graft_size} MM TUBULAR GRAFT "
            "WAS ANASTOMOSED DISTALLY TO THE AORTA USING 3/0 PROLENE. SUBSEQUENTLY, "
            "THE PROXIMAL END OF THE TUBULAR GRAFT WAS ANASTOMOSED TO THE PROXIMAL "
            "AORTA USING 4/0 PROLENE IN CONTINUOUS FASHION. AN AORTIC NEEDLE WAS "
            "PLACED ON THE GRAFT, DEAIRING WAS PERFORMED, AORTIC CROSS-CLAMP WAS "
            "REMOVED. AFTER {defibrillation_count} DEFIBRILLATIONS, THE HEART RESUMED "
            "NORMAL SINUS RHYTHM ({cross_clamp_time} MINUTES). THE PATIENT WAS WEANED "
            "FROM CPB IN A CONTROLLED MANNER ({cpb_time} MINUTES). AFTER HEMOSTASIS, "
            "PROTAMINE WAS ADMINISTERED TO NEUTRALIZE HEPARIN AND DECANNULATION WAS "
            "PERFORMED. EPICARDIAL PACEMAKER WIRES WERE PLACED. AFTER GENERAL "
            "HEMOSTASIS, {drain_placement} DRAINS WERE PLACED IN THE MEDIASTINUM. THE "
            "STERNUM WAS WIRED AND SKIN-SUBCUTANEOUS LAYERS WERE CLOSED IN ANATOMICAL "
            "LAYERS. INTRAOPERATIVE BLOOD LOSS: {blood_loss} ML."
        ),
        "keywords": ["avr", "asendan aort", "ascending aorta", "suprakoroner",
                     "supracoronary", "replasman", "replacement", "tüp greft",
                     "tubular graft", "david", "koroner reimplantasyon", "coronary"]
    }
}


# ============================================================
# YARDIMCI FONKSIYONLAR
# ============================================================

def detect_template(text: str) -> str:
    text_lower = text.lower()
    scores = {}
    for key, tmpl in KDC_TEMPLATES.items():
        score = sum(1 for kw in tmpl["keywords"] if kw.lower() in text_lower)
        scores[key] = score
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "CABG"


def get_or_create_user(email: str, ip: str = None) -> dict:
    if email not in users_db:
        if ip:
            for other_email, other in users_db.items():
                if other.get("ip") == ip and other.get("plan") == "trial":
                    raise HTTPException(status_code=403, detail="Bu IP adresinden zaten bir hesap olusturulmus.")

        users_db[email] = {
            "credits": 1,
            "plan": "trial",
            "plan_expires": time.time() + (7 * 24 * 3600),
            "created_at": time.time(),
            "ip": ip
        }
        save_users()
    return users_db[email]


def check_access(email: str, ip: str = None) -> tuple[bool, str]:
    user = get_or_create_user(email, ip)

    if user["plan_expires"] and time.time() > user["plan_expires"]:
        if user["plan"] != "trial":
            user["credits"] = 0
            user["plan"] = "expired"

    if ip and user["plan"] == "trial" and user["credits"] > 0:
        for other_email, other in users_db.items():
            if other_email != email and other.get("ip") == ip:
                if other.get("plan") == "trial":
                    return False, "duplicate_ip"

    if user["credits"] > 0:
        return True, user["plan"]
    return False, "expired"


# ============================================================
# API MODELLERI - FIXED
# ============================================================

class ReportRequest(BaseModel):
    text: str
    template: str = "auto"
    language: str = "English"
    email: str


class PolarCheckoutRequest(BaseModel):
    email: str  # <-- FIXED: email eklendi
    plan: str = "mini"
    success_url: str = "https://vascuscribe.com/success"
    return_url: str = "https://vascuscribe.com/pricing"


class CustomerPortalRequest(BaseModel):
    email: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str


# ============================================================
# AUTH ENDPOINTLERI
# ============================================================

@app.post("/register")
async def register(req: RegisterRequest):
    """Yeni kullanici kaydi"""
    if req.email in users_db and users_db[req.email].get("password_hash"):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayitli")

    password_hash = hash_password(req.password)

    if req.email not in users_db:
        users_db[req.email] = {
            "credits": 1,
            "plan": "trial",
            "plan_expires": time.time() + (7 * 24 * 3600),
            "created_at": time.time(),
            "ip": None,
            "password_hash": password_hash
        }
    else:
        users_db[req.email]["password_hash"] = password_hash

    save_users()
    token = create_access_token({"sub": req.email})

    return {
        "success": True,
        "email": req.email,
        "token": token,
        "credits": users_db[req.email]["credits"],
        "plan": users_db[req.email]["plan"]
    }


@app.post("/login")
async def login(req: LoginRequest):
    """Kullanici girisi - JWT token doner"""
    if req.email not in users_db:
        raise HTTPException(status_code=401, detail="Kullanici bulunamadi")

    user = users_db[req.email]

    if not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Sifre ayarlanmamis, once kayit olun")

    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Hatali sifre")

    if user["plan_expires"] and time.time() > user["plan_expires"]:
        if user["plan"] != "trial":
            user["credits"] = 0
            user["plan"] = "expired"
            save_users()

    token = create_access_token({"sub": req.email})

    return {
        "success": True,
        "email": req.email,
        "token": token,
        "credits": user["credits"],
        "plan": user["plan"],
        "expires": user["plan_expires"]
    }


@app.get("/me")
async def me(email: str = Depends(verify_token)):
    """Token ile kullanici bilgisi"""
    if email not in users_db:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    user = users_db[email]

    if user["plan_expires"] and time.time() > user["plan_expires"]:
        if user["plan"] != "trial":
            user["credits"] = 0
            user["plan"] = "expired"
            save_users()

    return {
        "email": email,
        "credits": user["credits"],
        "plan": user["plan"],
        "expires": user["plan_expires"],
        "can_use": user["credits"] > 0
    }


# ============================================================
# DIGER ENDPOINTLER
# ============================================================

@app.post("/transcribe")
async def transcribe_audio(
    request: Request,
    audio: UploadFile = File(...),
    email: str = Form(...)
):
    client_ip = request.client.host
    allowed, status = check_access(email, ip=client_ip)
    if not allowed:
        raise HTTPException(status_code=403, detail="Kota doldu veya IP limiti asildi.")

    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="Sunucu yapilandirma hatasi")

    temp_path = None
    try:
        temp_path = f"temp_audio_{int(time.time())}.wav"
        with open(temp_path, "wb") as f:
            content = await audio.read()
            f.write(content)

        with open(temp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="tr"
            )

        user = users_db[email]
        user["credits"] -= 1
        save_users()

        return {
            "text": result.text,
            "credits_remaining": user["credits"],
            "plan": user["plan"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.post("/generate")
async def generate_report(req: ReportRequest, request: Request):
    client_ip = request.client.host
    allowed, status = check_access(req.email, ip=client_ip)
    if not allowed:
        raise HTTPException(status_code=403, detail="Kota doldu veya IP limiti asildi.")

    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="Sunucu yapilandirma hatasi")

    try:
        template = req.template
        if template == "auto":
            template = detect_template(req.text)

        sablonlar = ""
        for k, v in KDC_TEMPLATES.items():
            sablonlar += f"\n{k}: {v['name']}\n{v['template']}\n"

        sys_prompt = (
            "You are a senior Cardiovascular Surgery professor. Analyze the user's "
            "dictated operation notes and generate a standard epicrisis report.\n\n"
            "TASK:\n"
            "1. Analyze the raw dictated text\n"
            "2. Remove meaningless, parasitic, or repeated words\n"
            "3. Preserve critical data (vessel names, durations, graft details, valve sizes)\n"
            "4. Select the most appropriate template below\n"
            "5. Insert critical data into the template\n"
            "6. Output in professional, formal epicrisis language\n\n"
            "TEMPLATES:\n" + sablonlar + "\n"
            "RULES:\n"
            "- NEVER REMOVE durations, vessel names (LAD, CX, RCA, LIMA, SVG), graft counts\n"
            "- Preserve template structure, only update variable fields\n"
            "- CRITICAL: The entire response MUST be in " + req.language + " language\n"
            "- Do NOT use English words unless they are medical terms (e.g., LAD, LIMA, SVG)\n"
            "- Use formal, professional medical epicrisis style in " + req.language + "\n"
            "OUTPUT: Return only the epicrisis text, no explanations."
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": req.text}
            ],
            temperature=0.1,
            max_tokens=2000
        )

        report = response.choices[0].message.content.strip()

        user = users_db[req.email]
        user["credits"] -= 1
        save_users()

        return {
            "report": report,
            "template_used": KDC_TEMPLATES.get(template, {}).get("name", template),
            "credits_remaining": user["credits"],
            "plan": user["plan"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/user-status")
async def user_status(email: str, request: Request):
    client_ip = request.client.host
    user = get_or_create_user(email, ip=client_ip)

    if user["plan_expires"] and time.time() > user["plan_expires"]:
        if user["plan"] != "trial":
            user["credits"] = 0
            user["plan"] = "expired"

    return {
        "email": email,
        "credits": user["credits"],
        "plan": user["plan"],
        "expires": user["plan_expires"],
        "can_use": user["credits"] > 0
    }


# ============================================================
# POLAR CHECKOUT - ROBUST VERSION WITH FALLBACK
# ============================================================

@app.post("/create-checkout")
async def create_checkout(req: PolarCheckoutRequest):
    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi - .env dosyasini kontrol edin")

    price_id = POLAR_PRODUCTS.get(req.plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Gecersiz plan '{req.plan}' veya fiyat ID ayarlanmamis. .env'deki POLAR_*_PRICE_ID degerlerini kontrol edin.")

    try:
        customer = None
        customer_id = None

        # Try to find existing customer
        try:
            customers_res = polar_client.customers.list(email=req.email)
            if hasattr(customers_res, 'items') and customers_res.items:
                for c in customers_res.items:
                    if hasattr(c, 'email') and c.email == req.email:
                        customer = c
                        break
            elif hasattr(customers_res, 'result') and hasattr(customers_res.result, 'items'):
                for c in customers_res.result.items:
                    if hasattr(c, 'email') and c.email == req.email:
                        customer = c
                        break
        except Exception as e:
            print(f"Customer search warning: {e}")

        # Create customer if not found
        if not customer:
            try:
                create_req = {
                    "email": req.email,
                    "external_id": req.email,
                }
                # Try with type field (newer SDK versions)
                try:
                    customer = polar_client.customers.create(request={**create_req, "type": "individual"})
                except Exception:
                    customer = polar_client.customers.create(request=create_req)
            except Exception as e:
                print(f"Customer create error: {e}")
                raise HTTPException(status_code=500, detail=f"Musteri olusturulamadi: {str(e)}")

        customer_id = customer.id if hasattr(customer, 'id') else customer.get('id')

        if not customer_id:
            raise HTTPException(status_code=500, detail="Musteri ID alinamadi")

        # Create checkout
        checkout_req = {
            "products": [price_id],
            "success_url": req.success_url,
            "return_url": req.return_url,
            "customer_id": customer_id,
            "customer_metadata": {"email": req.email, "plan": req.plan},
            "metadata": {"app": "vascuscribe", "plan": req.plan, "email": req.email}
        }

        checkout = polar_client.checkouts.create(request=checkout_req)

        checkout_url = checkout.url if hasattr(checkout, 'url') else checkout.get('url')
        checkout_id = checkout.id if hasattr(checkout, 'id') else checkout.get('id')

        return {
            "success": True,
            "checkout_url": checkout_url,
            "checkout_id": checkout_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Checkout olusturulamadi: {str(e)}")


# ============================================================
# MANUAL SUBSCRIPTION SYNC (for when webhooks don't work)
# ============================================================

@app.post("/sync-subscription")
async def sync_subscription(request: Request):
    """
    Polar API'den musterinin abonelik durumunu cekerek kredileri gunceller.
    Webhook calismadiginda kullanilir.
    """
    data = await request.json()
    email = data.get("email")

    if not email or email not in users_db:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi")

    try:
        # Find customer by email
        customer = None
        try:
            customers_res = polar_client.customers.list(email=email)
            if hasattr(customers_res, 'items') and customers_res.items:
                for c in customers_res.items:
                    if hasattr(c, 'email') and c.email == email:
                        customer = c
                        break
        except Exception as e:
            print(f"Sync - customer search error: {e}")

        if not customer:
            return {"synced": False, "message": "Polar'da musteri bulunamadi"}

        customer_id = customer.id if hasattr(customer, 'id') else customer.get('id')

        # Get customer state (includes subscriptions)
        try:
            state = polar_client.customers.get_state(id=customer_id)

            # Check for active subscriptions
            has_active = False
            plan_name = None

            if hasattr(state, 'subscriptions') and state.subscriptions:
                for sub in state.subscriptions:
                    sub_status = sub.status if hasattr(sub, 'status') else sub.get('status', '')
                    if sub_status in ['active', 'trialing']:
                        has_active = True
                        # Try to determine plan from product
                        product_name = ''
                        if hasattr(sub, 'product') and sub.product:
                            product_name = sub.product.name if hasattr(sub.product, 'name') else str(sub.product)

                        # Map product to plan
                        for plan_key in PLANS.keys():
                            if plan_key in product_name.lower():
                                plan_name = plan_key
                                break
                        if not plan_name:
                            plan_name = 'mini'  # default
                        break

            if has_active and plan_name:
                plan_config = PLANS[plan_name]
                user = users_db[email]
                user["credits"] = plan_config["credits"]
                user["plan"] = plan_name
                user["plan_expires"] = time.time() + (plan_config["days"] * 24 * 3600)
                user["polar_customer_id"] = customer_id
                save_users()

                return {
                    "synced": True,
                    "plan": plan_name,
                    "credits": plan_config["credits"],
                    "message": f"Abonelik senkronize edildi: {plan_name}"
                }
            else:
                return {"synced": False, "message": "Aktif abonelik bulunamadi"}

        except Exception as e:
            print(f"Sync - get_state error: {e}")
            return {"synced": False, "message": f"Durum alinamadi: {str(e)}"}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# DEV ONLY: Test credits endpoint (remove in production)
# ============================================================

@app.post("/dev-add-credits")
async def dev_add_credits(request: Request):
    """
    SADECE GELISTIRME ICIN: Test kredisi ekler.
    Production'da kaldirilmalidir!
    """
    data = await request.json()
    email = data.get("email")
    plan = data.get("plan", "mini")

    if not email or email not in users_db:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    if plan not in PLANS:
        raise HTTPException(status_code=400, detail="Gecersiz plan")

    plan_config = PLANS[plan]
    user = users_db[email]
    user["credits"] += plan_config["credits"]
    user["plan"] = plan
    user["plan_expires"] = time.time() + (plan_config["days"] * 24 * 3600)
    save_users()

    return {
        "success": True,
        "credits": user["credits"],
        "plan": user["plan"],
        "message": f"TEST: {plan_config['credits']} kredi eklendi"
    }


@app.post("/customer-portal")
async def customer_portal(req: CustomerPortalRequest):
    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi")

    if not req.email or req.email not in users_db:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    user = users_db[req.email]
    polar_customer_id = user.get("polar_customer_id")

    if not polar_customer_id:
        raise HTTPException(status_code=400, detail="Aktif Polar aboneligi yok")

    try:
        session = polar_client.customer_sessions.create(request={
            "customer_id": polar_customer_id
        })
        return {"portal_url": session.url if hasattr(session, 'url') else session.get('url')}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# POLAR WEBHOOK
# ============================================================

@app.post("/polar-webhook")
async def polar_webhook(request: Request):
    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi")

    if not POLAR_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret ayarlanmamis")

    payload = await request.body()
    headers = dict(request.headers)

    try:
        event = validate_event(
            payload=payload,
            headers=headers,
            secret=POLAR_WEBHOOK_SECRET,
        )
    except WebhookVerificationError:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook validation error: {str(e)}")

    event_type = event.type
    data = event.data

    print(f"📩 Polar webhook received: {event_type}")

    def get_metadata(data_obj):
        metadata = {}
        if hasattr(data_obj, 'metadata') and data_obj.metadata:
            metadata = data_obj.metadata if isinstance(data_obj.metadata, dict) else {}
        if hasattr(data_obj, 'customer_metadata') and data_obj.customer_metadata:
            cm = data_obj.customer_metadata if isinstance(data_obj.customer_metadata, dict) else {}
            metadata.update(cm)
        return metadata

    metadata = get_metadata(data)
    email = metadata.get("email")
    plan = metadata.get("plan", "mini")

    if not email and hasattr(data, 'customer') and data.customer:
        if hasattr(data.customer, 'external_id') and data.customer.external_id:
            email = data.customer.external_id
        elif hasattr(data.customer, 'email') and data.customer.email:
            email = data.customer.email

    print(f"📧 Email: {email}, Plan: {plan}")

    if event_type in ["subscription.active", "subscription.created", "subscription.updated"]:
        if email and plan in PLANS:
            plan_config = PLANS[plan]
            user = get_or_create_user(email)
            user["credits"] += plan_config["credits"]
            user["plan"] = plan
            user["plan_expires"] = time.time() + (plan_config["days"] * 24 * 3600)
            user["polar_subscription_id"] = str(data.id) if hasattr(data, 'id') else None
            user["polar_customer_id"] = str(data.customer_id) if hasattr(data, 'customer_id') else None
            save_users()
            print(f"✅ Subscription activated/updated: {email} -> {plan}")

    elif event_type in ["subscription.revoked", "subscription.canceled"]:
        if email and email in users_db:
            users_db[email]["plan"] = "expired"
            users_db[email]["credits"] = 0
            save_users()
            print(f"❌ Subscription revoked: {email}")

    elif event_type in ["order.paid", "checkout.completed"]:
        if email and plan in PLANS:
            plan_config = PLANS[plan]
            user = get_or_create_user(email)
            user["credits"] += plan_config["credits"]
            user["plan"] = plan
            user["plan_expires"] = time.time() + (plan_config["days"] * 24 * 3600)
            save_users()
            print(f"💰 Order paid: {email} -> {plan}")

    return {"status": "success"}


# ============================================================
# LEGACY CHECKOUT ENDPOINT - ROBUST
# ============================================================

@app.post("/create-checkout-session")
async def create_checkout_legacy(req: Request):
    data = await req.json()
    email = data.get("email")
    plan = data.get("plan", "mini")

    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi - .env dosyasini kontrol edin")

    price_id = POLAR_PRODUCTS.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Gecersiz plan '{plan}' veya fiyat ID ayarlanmamis")

    try:
        customer = None

        try:
            customers_res = polar_client.customers.list(email=email)
            if hasattr(customers_res, 'items') and customers_res.items:
                for c in customers_res.items:
                    if hasattr(c, 'email') and c.email == email:
                        customer = c
                        break
            elif hasattr(customers_res, 'result') and hasattr(customers_res.result, 'items'):
                for c in customers_res.result.items:
                    if hasattr(c, 'email') and c.email == email:
                        customer = c
                        break
        except Exception:
            pass

        if not customer:
            try:
                create_req = {"email": email, "external_id": email}
                try:
                    customer = polar_client.customers.create(request={**create_req, "type": "individual"})
                except Exception:
                    customer = polar_client.customers.create(request=create_req)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Musteri olusturulamadi: {str(e)}")

        customer_id = customer.id if hasattr(customer, 'id') else customer.get('id')

        checkout = polar_client.checkouts.create(request={
            "products": [price_id],
            "success_url": "https://vascuscribe.com/success?email=" + email,
            "return_url": "https://vascuscribe.com/",
            "customer_id": customer_id,
            "metadata": {"app": "vascuscribe", "plan": plan, "email": email},
        })

        return {
            "success": True,
            "checkout_url": checkout.url if hasattr(checkout, 'url') else checkout.get('url'),
            "checkout_id": checkout.id if hasattr(checkout, 'id') else checkout.get('id'),
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Checkout olusturulamadi: {str(e)}")


# ============================================================
# ROOT ENDPOINT - index.html servis et
# ============================================================

@app.get("/")
async def root():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return HTMLResponse("<h1>VascuScribe API</h1><p>index.html bulunamadi</p>")

@app.get("/success")
async def success(email: str = None):
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Odeme Basarili - VascuScribe</title>
        <meta charset="UTF-8">
        <style>
            body {
                background: #0a0a0a;
                color: #20e3b2;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                text-align: center;
                padding-top: 100px;
                margin: 0;
            }
            h1 { font-size: 32px; margin-bottom: 16px; }
            p { color: #888; font-size: 16px; margin-bottom: 8px; }
            .spinner {
                width: 40px;
                height: 40px;
                border: 3px solid #333;
                border-top-color: #20e3b2;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin: 30px auto;
            }
            @keyframes spin { to { transform: rotate(360deg); } }
            .btn {
                display: inline-block;
                margin-top: 20px;
                padding: 12px 24px;
                background: linear-gradient(135deg, #20e3b2, #0a8f6e);
                color: #000;
                text-decoration: none;
                border-radius: 12px;
                font-weight: 600;
            }
        </style>
    </head>
    <body>
        <div class="spinner"></div>
        <h1>✅ Odeme Basarili!</h1>
        <p>Kredileriniz yukleniyor...</p>
        <p>Lutfen bekleyin, otomatik yonlendirme yapilacak...</p>
        <a href="https://vascuscribe.com/" class="btn">Ana Sayfaya Don</a>
        <script>
            setTimeout(() => {
                window.location.href = 'https://vascuscribe.com/';
            }, 5000);
        </script>
    </body>
    </html>
    """)



@app.get("/robots.txt", response_class=FileResponse)
async def robots_txt():
    return FileResponse("static/robots.txt")

@app.get("/sitemap.xml", response_class=FileResponse)
async def sitemap_xml():
    return FileResponse("static/sitemap.xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

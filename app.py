"""
VascuScribe - Vascular Surgery Note Assistant
Backend API v3.1 - Polar API Fix + Landing Page Enhanced
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI

# ============ JWT EKLENTILERI ============
from jose import jwt, JWTError
import bcrypt

# ============ POSTGRESQL EKLENTISI ============
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# ============================================================
# DATABASE YARDIMCI FONKSIYONLARI
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "")
_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None and DATABASE_URL:
        _db_pool = ConnectionPool("postgresql://" + DATABASE_URL.replace("postgresql://", ""), min_size=1, max_size=10)
    return _db_pool

def get_db():
    """Veritabani baglantisi al"""
    pool = get_db_pool()
    if pool:
        return pool.getconn()
    return None

def release_db(conn):
    """Veritabani baglantisini geri ver"""
    pool = get_db_pool()
    if pool and conn:
        pool.putconn(conn)

def init_db():
    """Veritabanini baslat ve tablolari olustur"""
    conn = get_db()
    if not conn:
        print("UYARI: DATABASE_URL ayarlanmamis, JSON moduna dusuyor")
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email VARCHAR(255) PRIMARY KEY,
                credits INTEGER DEFAULT 0,
                plan VARCHAR(50) DEFAULT 'trial',
                plan_expires TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip VARCHAR(50),
                password_hash VARCHAR(255)
            )
        """)
        conn.commit()
        cur.close()
        print("PostgreSQL tablolari olusturuldu")
        return True
    except Exception as e:
        print(f"Veritabani baslatma hatasi: {e}")
        return False
    finally:
        release_db(conn)

def db_get_user(email: str) -> dict:
    """Kullanici bilgilerini veritabanindan al"""
    conn = get_db()
    if not conn:
        return None
    try:
        cur = conn.cursor(row_factory=dict_row)
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        cur.close()
        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"DB get_user hatasi: {e}")
        return None
    finally:
        release_db(conn)

def db_create_user(email: str, password_hash: str = None, ip: str = None,
                   credits: int = 1, plan: str = "trial", plan_expires = None) -> dict:
    """Yeni kullanici olustur"""
    conn = get_db()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        if plan_expires is None:
            plan_expires = datetime.utcnow() + timedelta(days=7)

        cur.execute("""
            INSERT INTO users (email, password_hash, ip, credits, plan, plan_expires, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
            RETURNING email
        """, (email, password_hash, ip, credits, plan, plan_expires, datetime.utcnow()))
        conn.commit()
        cur.close()
        return db_get_user(email)
    except Exception as e:
        print(f"DB create_user hatasi: {e}")
        conn.rollback()
        return None
    finally:
        release_db(conn)

def db_update_user(email: str, **kwargs) -> bool:
    """Kullanici bilgilerini guncelle"""
    conn = get_db()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        fields = []
        values = []
        for key, val in kwargs.items():
            fields.append(f"{key} = %s")
            values.append(val)
        values.append(email)

        query = f"UPDATE users SET {', '.join(fields)} WHERE email = %s"
        cur.execute(query, values)
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"DB update_user hatasi: {e}")
        conn.rollback()
        return False
    finally:
        release_db(conn)

def db_get_all_users() -> list:
    """Tum kullanicilari listele"""
    conn = get_db()
    if not conn:
        return []
    try:
        cur = conn.cursor(row_factory=dict_row)
        cur.execute("SELECT * FROM users")
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"DB get_all_users hatasi: {e}")
        return []
    finally:
        release_db(conn)

# ============================================================
# JSON YEDEK (PostgreSQL calismazsa)
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

# ============================================================
# KARMA FONKSIYONLAR (DB varsa DB, yoksa JSON)
# ============================================================
_db_available = None

def db_available() -> bool:
    global _db_available
    if _db_available is None:
        _db_available = init_db()
    return _db_available

def get_user(email: str) -> dict:
    """Kullanici getir - DB oncelikli"""
    if db_available():
        user = db_get_user(email)
        if user:
            if user.get('plan_expires'):
                user['plan_expires'] = user['plan_expires'].timestamp() if isinstance(user['plan_expires'], datetime) else user['plan_expires']
            if user.get('created_at'):
                user['created_at'] = user['created_at'].timestamp() if isinstance(user['created_at'], datetime) else user['created_at']
            return user
    return users_db.get(email)

def create_user(email: str, password_hash: str = None, ip: str = None,
                credits: int = 1, plan: str = "trial", plan_expires = None) -> dict:
    """Kullanici olustur - DB oncelikli"""
    if plan_expires is None:
        plan_expires = time.time() + (7 * 24 * 3600)

    if db_available():
        db_expires = datetime.fromtimestamp(plan_expires) if isinstance(plan_expires, (int, float)) else plan_expires
        db_create_user(email, password_hash, ip, credits, plan, db_expires)

    users_db[email] = {
        "credits": credits,
        "plan": plan,
        "plan_expires": plan_expires,
        "created_at": time.time(),
        "ip": ip,
        "password_hash": password_hash
    }
    save_users()
    return users_db[email]

def update_user(email: str, **kwargs) -> bool:
    """Kullanici guncelle - DB oncelikli"""
    if email in users_db:
        users_db[email].update(kwargs)
        save_users()

    if db_available():
        db_kwargs = {}
        for k, v in kwargs.items():
            if k == 'plan_expires' and isinstance(v, (int, float)):
                db_kwargs[k] = datetime.fromtimestamp(v)
            else:
                db_kwargs[k] = v
        return db_update_user(email, **db_kwargs)
    return True

def user_exists(email: str) -> bool:
    if db_available():
        return db_get_user(email) is not None
    return email in users_db

def get_all_users() -> dict:
    if db_available():
        db_users = db_get_all_users()
        result = {}
        for u in db_users:
            email = u['email']
            if u.get('plan_expires') and isinstance(u['plan_expires'], datetime):
                u['plan_expires'] = u['plan_expires'].timestamp()
            if u.get('created_at') and isinstance(u['created_at'], datetime):
                u['created_at'] = u['created_at'].timestamp()
            result[email] = u
        return result
    return users_db

# ============================================================
# SIFRE FONKSIYONLARI
# ============================================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
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
# ============================================================
# RATE LIMITING - Basit in-memory
# ============================================================
from collections import defaultdict
import time

class SimpleRateLimiter:
    """Basit in-memory rate limiter"""
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        # Eski istekleri temizle
        self.requests[key] = [t for t in self.requests[key] if now - t < self.window_seconds]

        if len(self.requests[key]) >= self.max_requests:
            return False

        self.requests[key].append(now)
        return True

rate_limiter = SimpleRateLimiter(max_requests=30, window_seconds=60)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    # Webhook ve health check'leri atla
    if request.url.path in ["/polar-webhook", "/", "/success", "/robots.txt", "/sitemap.xml"]:
        return await call_next(request)

    client_ip = request.client.host
    if not rate_limiter.is_allowed(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Çok fazla istek. Lütfen biraz bekleyin."},
            headers={"Retry-After": "60"}
        )

    return await call_next(request)



app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://vascuscribe.com",
        "https://www.vascuscribe.com",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
    allow_credentials=True,
)

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_KEY:
    print("UYARI: OPENAI_API_KEY ayarlanmamis!")

client = OpenAI(api_key=OPENAI_KEY)

# ============================================================
# RESEND EMAIL ENTEGRASYONU
# ============================================================
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@vascuscribe.com")

def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Resend ile email gonder"""
    if not RESEND_API_KEY:
        print(f"UYARI: RESEND_API_KEY ayarlanmamis, email gonderilemedi: {subject}")
        return False

    try:
        import requests
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html_content
            },
            timeout=10
        )
        if res.status_code == 200:
            print(f"Email gonderildi: {to_email} - {subject}")
            return True
        else:
            print(f"Email hatasi: {res.status_code} - {res.text}")
            return False
    except Exception as e:
        print(f"Email gonderim hatasi: {e}")
        return False



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
    print("Polar SDK yuklendi")
except ImportError as e:
    Polar = None
    validate_event = None
    WebhookVerificationError = Exception
    POLAR_AVAILABLE = False
    print(f"Polar SDK yuklenemedi: {e}")

POLAR_ACCESS_TOKEN = os.getenv("POLAR_ACCESS_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.getenv("POLAR_WEBHOOK_SECRET", "")

polar_client = None
if POLAR_AVAILABLE and POLAR_ACCESS_TOKEN:
    try:
        polar_client = Polar(
            access_token=POLAR_ACCESS_TOKEN,
            server=os.getenv("POLAR_SERVER", "sandbox")
        )
        print("Polar client basariyla olusturuldu")
    except Exception as e:
        print(f"Polar client olusturulamadi: {e}")
        polar_client = None
else:
    print("Polar devre disi: TOKEN eksik veya SDK yuklenemedi")

# ============================================================
# POLAR PRODUCT ID'LERI (Price ID degil, Product ID!)
# ============================================================
POLAR_PRODUCTS = {
    "mini":       os.getenv("POLAR_MINI_PRICE_ID", "21528818-9fd6-4a99-8b6c-1baa84a73a75"),
    "standard":   os.getenv("POLAR_STANDARD_PRICE_ID", "dba65d7f-5b09-455d-939a-2d7e63845136"),
    "pro":        os.getenv("POLAR_PRO_PRICE_ID", "dca9a64e-12c5-49e4-bcbe-92ee005528e9"),
    "enterprise": os.getenv("POLAR_ENTERPRISE_PRICE_ID", "0f86bc4a-396d-430b-89f3-a2551898dd7f"),
}

# ============================================================
# KOTA SISTEMI
# ============================================================
load_users()

PLANS = {
    "trial":      {"price": 0,    "credits": 1,  "days": 0},
    "mini":       {"price": 600,  "credits": 3,  "days": 0},
    "standard":   {"price": 1500, "credits": 10, "days": 0},
    "pro":        {"price": 3600, "credits": 25, "days": 0},
    "enterprise": {"price": 6900, "credits": 50, "days": 0},
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
    user = get_user(email)
    if not user:
        # Yeni kullanıcıya trial kredisi ver
        create_user(email, ip=ip, credits=1, plan="trial",
                     plan_expires=time.time() + (7 * 24 * 3600))
        user = get_user(email)
    return user


def check_access(email: str, ip: str = None) -> tuple:
    user = get_or_create_user(email, ip)

    # Trial kullanıcıları için süre kontrolü
    if user.get("plan") == "trial":
        plan_expires = user.get("plan_expires")
        if plan_expires and isinstance(plan_expires, (int, float)):
            if time.time() > plan_expires:
                # Trial süresi doldu, kredi varsa bile engelle
                return False, "trial_expired"

    # Kredi var mı kontrol et
    if user.get("credits", 0) > 0:
        return True, user.get("plan", "trial")
    return False, "expired"

# ============================================================
# API MODELLERI
# ============================================================

class ReportRequest(BaseModel):
    text: str
    template: str = "auto"
    language: str = "English"
    email: str


class PolarCheckoutRequest(BaseModel):
    email: str
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
    # Email validasyonu
    if not req.email or "@" not in req.email or "." not in req.email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Gecerli bir e-posta adresi girin")

    # Tek kullanimlik email domain kontrolu
    BLOCKED_DOMAINS = {
        "temp-mail.org", "tempmail.com", "10minutemail.com", "guerrillamail.com",
        "throwawaymail.com", "mailinator.com", "yopmail.com", "getairmail.com",
        "tempail.com", "fakeemail.net", "sharklasers.com", "getnada.com",
        "burnermail.io", "tempmailbox.net", "mail.tm", "mail.gw",
        "tmpmail.org", "disposablemail.com", "emailondeck.com", "tempinbox.com",
        "mailnesia.com", "tempmailaddress.com", "burner.kiwi", "trashmail.com",
        "mytemp.email", "spamgourmet.com", "maildrop.cc", "harakirimail.com",
        "mailcatch.com", "fakeinbox.com", "gettempmail.com", "tempm.com",
        "tempmailo.com", "tempmails.info", "temp-mail.io", "tmails.net",
        "test.com", "example.com", "localhost.com", "invalid.com"
    }

    domain = req.email.split("@")[-1].lower()
    if domain in BLOCKED_DOMAINS:
        raise HTTPException(status_code=400, detail="Gecerli bir e-posta adresi kullanın. Tek kullanimlik mailler kabul edilmez.")

    # Şifre güçlülüğü
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Sifre en az 6 karakter olmalidir")

    existing = get_user(req.email)
    if existing and existing.get("password_hash"):
        raise HTTPException(status_code=400, detail="Bu e-posta zaten kayitli")

    password_hash = hash_password(req.password)

    if not existing:
        create_user(req.email, password_hash=password_hash, credits=1, plan="trial",
                   plan_expires=time.time() + (7 * 24 * 3600))
    else:
        update_user(req.email, password_hash=password_hash)

    token = create_access_token({"sub": req.email})
    user = get_user(req.email)

    return {
        "success": True,
        "email": req.email,
        "token": token,
        "credits": user.get("credits", 0),
        "plan": user.get("plan", "trial")
    }


@app.post("/login")
async def login(req: LoginRequest):
    """Kullanici girisi - JWT token doner"""
    user = get_user(req.email)
    if not user:
        raise HTTPException(status_code=401, detail="Kullanici bulunamadi")

    if not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Sifre ayarlanmamis, once kayit olun")

    if not verify_password(req.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Hatali sifre")

    token = create_access_token({"sub": req.email})

    return {
        "success": True,
        "email": req.email,
        "token": token,
        "credits": user.get("credits", 0),
        "plan": user.get("plan", "trial"),
        "expires": user.get("plan_expires")
    }


# ============================================================
# SIFRE SIFIRLAMA - RESEND EMAIL ILE
# ============================================================
import secrets
import string

# Basit in-memory reset kodlari (production'da Redis/DB kullanin)
reset_codes = {}  # {email: {"code": "123456", "expires": timestamp}}

class ResetRequest(BaseModel):
    email: str

class ResetConfirmRequest(BaseModel):
    email: str
    code: str
    new_password: str

@app.post("/forgot-password")
async def forgot_password(req: ResetRequest):
    """Sifre sifirlama kodu gonder"""
    user = get_user(req.email)
    if not user:
        # Guvenlik icin kullanici yoksa bile ayni mesaj
        return {"success": True, "message": "E-posta adresinize sifirlama kodu gonderildi"}

    # 6 haneli kod uret
    code = ''.join(secrets.choice(string.digits) for _ in range(6))
    reset_codes[req.email] = {
        "code": code,
        "expires": time.time() + 600  # 10 dakika gecerli
    }

    # Email gonder
    email_sent = send_email(
        to_email=req.email,
        subject="VascuScribe - Sifre Sifirlama Kodu",
        html_content=f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #20e3b2;">VascuScribe Sifre Sifirlama</h2>
            <p>Sifre sifirlama talebiniz alindi.</p>
            <p style="font-size: 24px; font-weight: bold; letter-spacing: 4px; 
                      background: #f0f0f0; padding: 15px; text-align: center; 
                      border-radius: 8px; margin: 20px 0;">
                {code}
            </p>
            <p>Bu kod 10 dakika gecerlidir.</p>
            <p style="color: #888; font-size: 12px;">
                Bu talebi siz yapmadıysanız, lütfen bu e-postayı dikkate almayın.
            </p>
        </div>
        """
    )

    if email_sent:
        return {"success": True, "message": "E-posta adresinize sifirlama kodu gonderildi"}
    else:
        # Email gonderilemediyse log'a yaz ama kullaniciya gosterme (guvenlik)
        print(f"SIFIRLAMA KODU {req.email}: {code}")
        return {"success": True, "message": "E-posta adresinize sifirlama kodu gonderildi (Email servisi gecici olarak calismiyor, lutfen destek ile iletisime gecin)"}

@app.post("/reset-password")
async def reset_password(req: ResetConfirmRequest):
    """Yeni sifre ile sifirla"""
    reset_data = reset_codes.get(req.email)

    if not reset_data:
        raise HTTPException(status_code=400, detail="Sifirlama kodu bulunamadi veya suresi doldu")

    if time.time() > reset_data["expires"]:
        del reset_codes[req.email]
        raise HTTPException(status_code=400, detail="Sifirlama kodu suresi doldu")

    if req.code != reset_data["code"]:
        raise HTTPException(status_code=400, detail="Gecersiz sifirlama kodu")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="Sifre en az 6 karakter olmalidir")

    # Sifreyi guncelle
    password_hash = hash_password(req.new_password)
    update_user(req.email, password_hash=password_hash)

    # Kodu temizle
    del reset_codes[req.email]

    # Basarili emaili gonder
    send_email(
        to_email=req.email,
        subject="VascuScribe - Sifreniz Degistirildi",
        html_content="""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #20e3b2;">Sifre Degistirildi</h2>
            <p>Sifreniz basariyla degistirildi.</p>
            <p>Eger bu islemi siz yapmadıysanız, lütfen hemen destek ile iletisime gecin.</p>
        </div>
        """
    )

    return {"success": True, "message": "Sifreniz basariyla degistirildi"}

@app.get("/me")
async def me(email: str = Depends(verify_token)):
    """Token ile kullanici bilgisi"""
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    return {
        "email": email,
        "credits": user.get("credits", 0),
        "plan": user.get("plan", "trial"),
        "can_use": user.get("credits", 0) > 0
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
    # Email validasyonu
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Gecerli e-posta gerekli")

    client_ip = request.client.host
    allowed, status = check_access(email, ip=client_ip)
    if not allowed:
        if status == "trial_expired":
            raise HTTPException(status_code=403, detail="trial_expired")
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

        # Kredi düşürme - sadece generate'de düşürülüyor
        user = get_user(email)
        return {
            "text": result.text,
            "credits_remaining": user.get("credits", 0),
            "plan": user.get("plan", "trial")
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.post("/generate")
async def generate_report(req: ReportRequest, request: Request):
    # Email validasyonu
    if not req.email or "@" not in req.email:
        raise HTTPException(status_code=400, detail="Gecerli e-posta gerekli")

    client_ip = request.client.host
    allowed, status = check_access(req.email, ip=client_ip)
    if not allowed:
        if status == "trial_expired":
            raise HTTPException(status_code=403, detail="trial_expired")
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

        # DIL AYARI - KRITIK DUZELTME
        lang_instruction = req.language
        if req.language.lower() in ["turkish", "turkce", "türkçe", "tr"]:
            lang_instruction = "Turkish"
            output_lang = "Turkish"
        else:
            lang_instruction = "English"
            output_lang = "English"

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
            "- CRITICAL: The ENTIRE response MUST be written COMPLETELY in " + output_lang + " language ONLY\n"
            "- Do NOT use English words unless they are universal medical abbreviations (e.g., LAD, LIMA, SVG, CPB, AVR, MVR, CABG)\n"
            "- Do NOT mix languages - output must be 100% " + output_lang + "\n"
            "- Use formal, professional medical epicrisis style\n"
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

        user = get_user(req.email)
        new_credits = max(0, user.get("credits", 0) - 1)
        update_user(req.email, credits=new_credits)

        return {
            "report": report,
            "template_used": KDC_TEMPLATES.get(template, {}).get("name", template),
            "credits_remaining": new_credits,
            "plan": user.get("plan", "trial")
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/user-status")
async def user_status(email: str, request: Request):
    client_ip = request.client.host
    user = get_or_create_user(email, ip=client_ip)

    # Trial süresi kontrolü
    is_trial_expired = False
    if user.get("plan") == "trial":
        plan_expires = user.get("plan_expires")
        if plan_expires and isinstance(plan_expires, (int, float)):
            if time.time() > plan_expires:
                is_trial_expired = True

    return {
        "email": email,
        "credits": user.get("credits", 0),
        "plan": user.get("plan", "trial"),
        "can_use": user.get("credits", 0) > 0 and not is_trial_expired,
        "trial_expired": is_trial_expired,
        "plan_expires": user.get("plan_expires")
    }

# ============================================================
# POLAR CHECKOUT - FIXED FOR NEW API (products array)
# ============================================================

@app.post("/create-checkout")
async def create_checkout(req: PolarCheckoutRequest):
    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi - .env dosyasini kontrol edin")

    product_id = POLAR_PRODUCTS.get(req.plan)
    if not product_id:
        raise HTTPException(status_code=400, detail=f"Gecersiz plan '{req.plan}' veya Product ID ayarlanmamis. .env'deki POLAR_*_PRICE_ID degerlerini kontrol edin.")

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
                customer = polar_client.customers.create(request={
                    "email": req.email,
                    "external_id": req.email,
                })
            except Exception as e:
                print(f"Customer create error: {e}")
                raise HTTPException(status_code=500, detail=f"Musteri olusturulamadi: {str(e)}")

        customer_id = customer.id if hasattr(customer, 'id') else customer.get('id')

        if not customer_id:
            raise HTTPException(status_code=500, detail="Musteri ID alinamadi")

        # FIXED: Use products array instead of product_id (Polar 2026 API)
        checkout_req = {
            "products": [product_id],  # Array format - required by new API
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

    if not email or not get_user(email):
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
                update_user(email,
                    credits=plan_config["credits"],
                    plan=plan_name,
                    plan_expires=time.time() + (plan_config["days"] * 24 * 3600),
                    polar_customer_id=customer_id
                )

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





@app.post("/customer-portal")
async def customer_portal(req: CustomerPortalRequest):
    if not polar_client:
        raise HTTPException(status_code=500, detail="Polar yapilandirma hatasi")

    if not req.email or not get_user(req.email):
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    user = get_user(req.email)
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
# POLAR WEBHOOK - FIXED FOR NEW API
# ============================================================

@app.post("/polar-webhook")
async def polar_webhook(request: Request):
    try:
        if not polar_client:
            return {"status": "error", "message": "Polar yapilandirma hatasi"}

        if not POLAR_WEBHOOK_SECRET:
            return {"status": "error", "message": "Webhook secret ayarlanmamis"}

        payload = await request.body()
        headers = dict(request.headers)

        try:
            event = validate_event(
                payload=payload,
                headers=headers,
                secret=POLAR_WEBHOOK_SECRET,
            )
        except WebhookVerificationError:
            return {"status": "error", "message": "Invalid webhook signature"}
        except Exception as e:
            return {"status": "error", "message": f"Webhook validation error: {str(e)}"}

        event_type = event.type
        data = event.data

        print(f"Polar webhook received: {event_type}")

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
        plan = metadata.get("plan")

        # Fallback: email'i customer objesinden al
        if not email and hasattr(data, 'customer') and data.customer:
            if hasattr(data.customer, 'external_id') and data.customer.external_id:
                email = data.customer.external_id
            elif hasattr(data.customer, 'email') and data.customer.email:
                email = data.customer.email

        # Fallback: plan'i product'tan tespit et
        if not plan:
            product_name = ""
            if hasattr(data, 'product') and data.product:
                product_name = data.product.name if hasattr(data.product, 'name') else str(data.product)
            elif hasattr(data, 'product_id') and data.product_id:
                product_name = str(data.product_id)

            for plan_key in PLANS.keys():
                if plan_key in product_name.lower():
                    plan = plan_key
                    break
            if not plan:
                plan = "mini"

        print(f"Email: {email}, Plan: {plan}")

        if not email:
            print("Webhook: Email bulunamadi, islem yapilmiyor")
            return {"status": "skipped", "reason": "no_email"}

        # ============================================================
        # EVENT HANDLER - Her event tipi için ayrı mantık
        # ============================================================

        if event_type == "subscription.active":
            # Abonelik aktif olduğunda kredi ver
            if plan in PLANS:
                plan_config = PLANS[plan]
                user = get_or_create_user(email)
                # Mevcut krediyi koru, yeni kredi ekle (yenileme)
                new_credits = user.get("credits", 0) + plan_config["credits"]
                update_user(email,
                    credits=new_credits,
                    plan=plan,
                    plan_expires=time.time() + (plan_config["days"] * 24 * 3600),
                    polar_subscription_id=str(data.id) if hasattr(data, 'id') else None,
                    polar_customer_id=str(data.customer_id) if hasattr(data, 'customer_id') else None
                )
                print(f"Subscription activated: {email} -> {plan}, +{plan_config['credits']} credits")

        elif event_type == "subscription.created":
            # Abonelik oluşturuldu ama henüz aktif değil - kredi verme
            print(f"Subscription created (pending): {email}")

        elif event_type == "subscription.updated":
            # Plan değişikliğinde krediyi yenile
            if plan in PLANS:
                plan_config = PLANS[plan]
                user = get_or_create_user(email)
                update_user(email,
                    credits=plan_config["credits"],
                    plan=plan,
                    plan_expires=time.time() + (plan_config["days"] * 24 * 3600),
                    polar_subscription_id=str(data.id) if hasattr(data, 'id') else None,
                    polar_customer_id=str(data.customer_id) if hasattr(data, 'customer_id') else None
                )
                print(f"Subscription updated: {email} -> {plan}, credits set to {plan_config['credits']}")

        elif event_type in ["subscription.revoked", "subscription.canceled"]:
            if get_user(email):
                update_user(email, plan="expired", credits=0)
                print(f"Subscription revoked/canceled: {email}")

        # checkout.completed ve subscription.active AYRI event'ler
        # subscription.active zaten kredi eklediyse checkout.completed'ta tekrar ekleme
        elif event_type == "checkout.completed":
            # Sadece subscription olmayan (one-time) checkout'larda kredi ver
            is_subscription = False
            if hasattr(data, 'subscription_id') and data.subscription_id:
                is_subscription = True

            if not is_subscription and plan in PLANS:
                plan_config = PLANS[plan]
                user = get_or_create_user(email)
                new_credits = user.get("credits", 0) + plan_config["credits"]
                update_user(email,
                    credits=new_credits,
                    plan=plan,
                    plan_expires=time.time() + (plan_config["days"] * 24 * 3600)
                )
                print(f"One-time checkout completed: {email} -> {plan}")
            else:
                print(f"Checkout completed (subscription, kredi subscription.active'da eklenecek): {email}")

        elif event_type == "order.paid":
            # order.paid genellikle subscription ile birlikte gelir
            # subscription.active zaten kredi eklediyse tekrar ekleme
            if plan in PLANS:
                user = get_user(email)
                if user and user.get("plan") == "expired":
                    plan_config = PLANS[plan]
                    update_user(email,
                        credits=plan_config["credits"],
                        plan=plan,
                        plan_expires=time.time() + (plan_config["days"] * 24 * 3600)
                    )
                    print(f"Order paid (expired user): {email} -> {plan}")
                else:
                    print(f"Order paid (skipped, user already has plan): {email}")

        else:
            print(f"Unhandled webhook event: {event_type}")

        return {"status": "success"}

    except Exception as e:
        traceback.print_exc()
        # HATA OLSA BİLE 200 DÖN - Polar tekrar göndermez
        return {"status": "error", "message": str(e)}


# ============================================================
# LEGACY CHECKOUT ENDPOINT - FIXED FOR NEW API
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
                customer = polar_client.customers.create(request={
                    "email": email,
                    "external_id": email,
                })
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Musteri olusturulamadi: {str(e)}")

        customer_id = customer.id if hasattr(customer, 'id') else customer.get('id')

        # FIXED: Use products array for new API
        checkout = polar_client.checkouts.create(request={
            "products": [price_id],  # Array format for new API
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
# ADMIN ENDPOINTLERI (Kullanici yonetimi)
# ============================================================

ADMIN_KEY = os.getenv("ADMIN_KEY", "")

@app.get("/admin/users")
async def admin_users(key: str = Header(None, alias="X-Admin-Key")):
    """Tum kullanicilari listele (admin icin)"""
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Yetkisiz erisim")

    users = get_all_users()
    return {
        "count": len(users),
        "users": users
    }


@app.post("/admin/add-credits")
async def admin_add_credits(request: Request, key: str = Header(None, alias="X-Admin-Key")):
    """Admin: Kullaniciya kredi ekle"""
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Yetkisiz erisim")

    data = await request.json()
    email = data.get("email")
    credits = data.get("credits", 0)

    if not email or not get_user(email):
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    user = get_user(email)
    new_credits = user.get("credits", 0) + credits
    update_user(email, credits=new_credits)

    return {
        "success": True,
        "email": email,
        "credits": new_credits,
        "message": f"{credits} kredi eklendi. Yeni bakiye: {new_credits}"
    }


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
        <h1>Odeme Basarili!</h1>
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

# ============================================================
# GOOGLE OAUTH - Ileride Eklenecek (Opsiyonel)
# ============================================================
# Adimlar:
# 1. Google Cloud Console -> APIs & Services -> Credentials
# 2. OAuth 2.0 Client ID olustur (Web application)
# 3. Authorized redirect URIs: https://vascuscribe.com/auth/google/callback
# 4. Client ID ve Secret'i .env'ye ekle
# 5. Asagidaki endpoint'leri aktif et
# ============================================================

# from authlib.integrations.starlette_client import OAuth
# oauth = OAuth()
# oauth.register(
#     name='google',
#     client_id=os.getenv('GOOGLE_CLIENT_ID'),
#     client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
#     server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
#     client_kwargs={'scope': 'openid email profile'}
# )

# @app.get("/auth/google")
# async def google_login(request: Request):
#     redirect_uri = "https://vascuscribe.com/auth/google/callback"
#     return await oauth.google.authorize_redirect(request, redirect_uri)

# @app.get("/auth/google/callback")
# async def google_callback(request: Request):
#     token = await oauth.google.authorize_access_token(request)
#     user_info = token.get('userinfo')
#     email = user_info.get('email')
#     # Kullanici yoksa olustur, varsa giris yap
#     user = get_or_create_user(email)
#     jwt_token = create_access_token({"sub": email})
#     return {"token": jwt_token, "email": email}

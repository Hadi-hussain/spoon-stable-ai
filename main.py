import os
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

app = FastAPI(title="Spoon & Stable Concierge API")

# Enable CORS for frontend widgets
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Groq Client
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Database Path Configuration (Vercel serverless fix)
DB_PATH = "/tmp/restaurant.db" if os.environ.get("VERCEL") else "restaurant.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Table for reservations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            guests INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Table for knowledge base configuration
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL
        )
    ''')
    
    # Default Knowledge Base Text if table is empty
    default_kb = """
Restaurant Name: Spoon & Stable
Cuisine: French-inspired American Fine Dining
Hours: Mon-Sun 5:00 PM - 10:00 PM
Dress Code: Smart Casual
Cancellation Policy: Cancellations must be made at least 24 hours in advance.
Specialties: Wood-fired meats, handcrafted pasta, artisanal cocktails.
    """.strip()
    
    cursor.execute('''
        INSERT OR IGNORE INTO config (key, value) VALUES ('knowledge_base', ?)
    ''', (default_kb,))
    
    conn.commit()
    conn.close()

# Initialize DB structure on startup
init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- Data Models ---
class ChatRequest(BaseModel):
    message: str

class UpdateMenuRequest(BaseModel):
    new_text: str

# --- Endpoints ---

@app.get("/")
def home():
    return {"status": "Spoon & Stable AI Concierge API is active"}

# 1. Chat Endpoint
@app.post("/chat")
def chat(req: ChatRequest):
    if not client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY environment variable is missing.")

    # Fetch dynamic Knowledge Base from DB
    conn = get_db()
    kb_row = conn.execute("SELECT value FROM config WHERE key = 'knowledge_base'").fetchone()
    conn.close()
    
    kb_content = kb_row["value"] if kb_row else "Spoon & Stable Fine Dining Restaurant"

    system_prompt = f"""
You are the elite AI Concierge for 'Spoon & Stable', a high-end restaurant. 
Your tone is exceptionally warm, professional, sophisticated, and attentive.

Use the following Knowledge Base to answer guest queries:
{kb_content}

If the user wants to make a reservation, collect their:
1. Full Name
2. Phone Number
3. Date
4. Time
5. Number of Guests

Be helpful, elegant, and concise.
    """

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.message}
            ],
            temperature=0.7,
            max_tokens=300
        )
        bot_response = completion.choices[0].message.content
        return {"response": bot_response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 2. Admin Dashboard View
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found in root directory</h1>"

# 3. Admin: Get Reservations
@app.get("/admin/reservations")
def get_reservations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reservations ORDER BY created_at DESC").fetchall()
    conn.close()
    
    reservations = [dict(r) for r in rows]
    return {
        "total_reservations": len(reservations),
        "reservations": reservations
    }

# 4. Admin: Get Knowledge Base Configuration
@app.get("/admin/get-config")
def get_config():
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'knowledge_base'").fetchone()
    conn.close()
    return {"knowledge_base": row["value"] if row else ""}

# 5. Admin: Update Knowledge Base Configuration
@app.post("/admin/update-menu")
def update_menu(req: UpdateMenuRequest):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('knowledge_base', ?)", (req.new_text,))
    conn.commit()
    conn.close()
    return {"message": "Knowledge Base successfully updated!"}
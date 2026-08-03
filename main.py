import os
import sqlite3
import json
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

app = FastAPI(title="Spoon & Stable Concierge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

DB_PATH = "/tmp/restaurant.db" if os.environ.get("VERCEL") else "restaurant.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL
        )
    ''')
    
    default_kb = """
=== RESTAURANT OVERVIEW ===
Name: Spoon and Stable
Concept: French-inspired Midwestern cuisine set in a restored 1906 horse stable.
Owner & Executive Chef: Chef Gavin Kaysen (James Beard Award Winner)
Address: 211 N 1st St, Minneapolis, MN 55401 (North Loop Neighborhood)
Phone: (612) 224-9850
Email: info@spoonandstable.com
Website: https://www.spoonandstable.com

=== HOURS OF OPERATION ===
Dining Room Dinner Hours:
- Sunday – Thursday: 5:00 PM – 10:00 PM
- Friday & Saturday: 5:00 PM – 11:00 PM

The Parlour Bar & Lounge:
- Open daily starting at 4:00 PM until late.
- Walk-ins only (No reservations accepted for the Parlour Bar).

=== DINING MENU & PRICING ===
Starters:
- Bison Tartare ($24) - Harissa, quails egg, gaufrette potatoes
- Seared Sea Scallops ($28) - Cauliflower, golden raisin, caper butter
- Roasted Bone Marrow ($26) - Parsley salad, grilled sourdough
- Chilled Oysters ($24/half-dozen) - Mignonette, lemon, cocktail sauce

Handcrafted Pastas:
- Ricotta Cavatelli ($32) - Wild mushrooms, spinach, parmesan cream
- Garganelli ($34) - Heritage pork ragù, fennel, pecorino
- Spaghetti ($36) - Sungold tomatoes, blue crab, chili, breadcrumbs

Entrees / Wood-Fired Specialties:
- Heritage Pork Chop ($44) - Sweet potato, braised greens, cider reduction
- Duck Breast ($48) - Confit leg, roasted beets, cherry jus
- Dry-Aged Ribeye 14oz ($68) - Truffle butter, potato purée, roasted root vegetables
- Roasted Halibut ($46) - Leek fondue, fingerling potatoes, saffron broth

Desserts:
- Honey Crisp Apple Tart ($14) - Caramel, vanilla bean ice cream
- Dark Chocolate Ganache ($15) - Hazelnut crunch, espresso cream
- Artisanal Cheese Board ($22) - Honeycomb, seasonal fruit, crostini

Drinks & Beverage Program:
- Full artisanal cocktail menu ($16 - $20), extensive international wine list by bottle and glass, local Minnesota craft beers.

=== POLICIES & AMENITIES ===
- Dress Code: Smart Casual (no beachwear or athletic tank tops).
- Parking: Valet parking available at the main entrance ($15). Street parking and nearby public ramps available in the North Loop.
- Dietary Needs: Vegan, Vegetarian, Gluten-Free, and Nut-Free options are accommodated upon request.
- Reservation Policy: Reservations open 30 days in advance at midnight on OpenTable. Cancellations must be made at least 24 hours prior to avoid a $25 per person fee.
- Private Events & Dining: Private dining room accommodates up to 25 guests. For event inquiries, contact events@spoonandstable.com.
- Gift Cards: e-Gift cards and physical gift cards available online at spoonandstable.com or in person.
    """.strip()
    
    cursor.execute('''
        INSERT INTO config (key, value) VALUES ('knowledge_base', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    ''', (default_kb,))
    
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def save_reservation(name: str, phone: str, date: str, time: str, guests: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reservations (name, phone, date, time, guests) VALUES (?, ?, ?, ?, ?)",
        (name, phone, date, time, guests)
    )
    conn.commit()
    res_id = cursor.lastrowid
    conn.close()
    return res_id

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str = ""
    history: List[ChatMessage] = []

class ReservationRequest(BaseModel):
    name: str
    phone: str
    date: str
    time: str
    guests: int

class UpdateMenuRequest(BaseModel):
    new_text: str

@app.get("/", response_class=HTMLResponse)
def home():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found in root directory</h1>"

@app.post("/reserve")
def make_reservation(req: ReservationRequest):
    res_id = save_reservation(req.name, req.phone, req.date, req.time, req.guests)
    return {"status": "success", "reservation_id": res_id}

@app.post("/chat")
def chat(req: ChatRequest):
    if not client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY environment variable is missing.")

    conn = get_db()
    kb_row = conn.execute("SELECT value FROM config WHERE key = 'knowledge_base'").fetchone()
    conn.close()
    
    kb_content = kb_row["value"] if kb_row else "Spoon & Stable Fine Dining Restaurant"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "book_reservation",
                "description": "Call this ONLY when ALL 5 reservation details are clearly stated across history: Name, Phone, Date, Time, and Guest count.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Full name"},
                        "phone": {"type": "string", "description": "Phone number"},
                        "date": {"type": "string", "description": "Date"},
                        "time": {"type": "string", "description": "Time"},
                        "guests": {"type": "integer", "description": "Party size"}
                    },
                    "required": ["name", "phone", "date", "time", "guests"]
                }
            }
        }
    ]

    system_prompt = f"""
You are the elite AI Concierge for 'Spoon & Stable'.
Your tone is warm, polite, professional, and concise.

Knowledge Base:
{kb_content}

CRITICAL INSTRUCTIONS:
- Respond naturally to greetings like 'hi', 'hello', or 'hey' by welcoming the guest warmly.
- Keep all answers concise (2 to 3 sentences maximum).
- Look at the conversation history to track details provided so far.
- IF the user wants to book and ALL 5 details (Name, Phone, Date, Time, Guests) are present, call `book_reservation`.
- IF details are still missing for a booking, simply ask for the missing items in conversational text.
    """

    messages_payload = [{"role": "system", "content": system_prompt}]
    
    for item in req.history:
        if item.role in ["user", "assistant"] and item.content and item.content.strip():
            messages_payload.append({"role": item.role, "content": item.content})
            
    if req.message and (not req.history or req.history[-1].content != req.message):
        messages_payload.append({"role": "user", "content": req.message})

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages_payload,
            tools=tools,
            temperature=0.3,
            max_tokens=300
        )

        message = completion.choices[0].message

        # 1. Tool Execution Path
        if message.tool_calls:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "book_reservation":
                    try:
                        args = json.loads(tool_call.function.arguments)
                        name = args.get("name", "Guest")
                        phone = str(args.get("phone", ""))
                        date = args.get("date", "")
                        time = args.get("time", "")
                        raw_guests = args.get("guests", 1)
                        try:
                            guests = int(raw_guests)
                        except (ValueError, TypeError):
                            guests = 1

                        res_id = save_reservation(name=name, phone=phone, date=date, time=time, guests=guests)
                        return {
                            "response": f"Thank you, {name}! Your reservation for {guests} guest(s) on {date} at {time} is confirmed. (ID: #{res_id})"
                        }
                    except Exception as inner_e:
                        print(f"Tool Parsing Error: {inner_e}")

        # 2. Regular Text Response Path
        if message.content:
            return {"response": message.content}

        return {"response": "Good day! How may I assist you with Spoon & Stable today?"}

    except Exception as e:
        print(f"Chat Endpoint Exception: {e}")
        return {"response": "Hello! Welcome to Spoon & Stable. How may I assist you today?"}

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found in root directory</h1>"

@app.get("/admin/reservations")
def get_reservations():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reservations ORDER BY created_at DESC").fetchall()
    conn.close()
    return {
        "total_reservations": len(rows),
        "reservations": [dict(r) for r in rows]
    }

@app.get("/admin/get-config")
def get_config():
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = 'knowledge_base'").fetchone()
    conn.close()
    return {"knowledge_base": row["value"] if row else ""}

@app.post("/admin/update-menu")
def update_menu(req: UpdateMenuRequest):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('knowledge_base', ?)", (req.new_text,))
    conn.commit()
    conn.close()
    return {"message": "Knowledge Base successfully updated!"}
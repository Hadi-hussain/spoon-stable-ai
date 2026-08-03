import os
import sqlite3
import json
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
Name: Spoon & Stable
Cuisine: French-Inspired Midwestern Fine Dining (Helmed by James Beard Award-winning Chef Gavin Kaysen)
Address: 211 N 1st St, Minneapolis, MN 55401 (North Loop Neighborhood)
Phone: (612) 224-9850

=== HOURS OF OPERATION ===
Dinner:
- Sunday - Thursday: 5:00 PM – 10:00 PM
- Friday & Saturday: 5:00 PM – 11:00 PM
The Parlour Bar: Open daily from 4:00 PM until late (Walk-ins welcome, NO reservations).

=== MENU HIGHLIGHTS ===
Starters:
- Bison Tartare (harissa, quails egg, gaufrette potatoes)
- Seared Sea Scallops (cauliflower, golden raisin, caper butter)
- Roasted Bone Marrow (parsley salad, grilled sourdough)

Handcrafted Pasta:
- Ricotta Cavatelli (wild mushrooms, spinach, parmesan cream)
- Garganelli (heritage pork ragù, fennel, pecorino)

Mains / Wood-Fired Specialties:
- Heritage Pork Chop (sweet potato, braised greens, cider reduction)
- Duck Breast (confit leg, roasted beets, cherry jus)
- Dry-Aged Ribeye (truffle butter, potato puree, roasted roots)

Desserts:
- Honey Crisp Apple Tart (caramel, vanilla bean ice cream)
- Dark Chocolate Ganache (hazelnut crunch, espresso cream)

Drinks:
- Full artisanal cocktail menu, extensive international wine list, local craft beers.

=== POLICIES & AMENITIES ===
- Dress Code: Smart Casual (no beachwear or tank tops).
- Parking: Valet parking available at the main entrance ($15). Nearby street parking and ramps available.
- Dietary Needs: Vegan, Vegetarian, Gluten-Free, and Nut-Free options available upon request.
- Reservation Policy: Reservations open 30 days in advance at midnight. Cancellations must be made at least 24 hours prior to avoid a $25 per person cancellation fee.
- Private Dining: Private dining room accommodates up to 25 guests. Contact events@spoonandstable.com for bookings.
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

class ChatRequest(BaseModel):
    message: str

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
                "description": "Book a reservation ONLY when the guest explicitly provided ALL 5 items: Name, Phone, Date, Time, and Guest Count in their message. DO NOT guess missing fields.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Full name"},
                        "phone": {"type": "string", "description": "Phone number provided by user"},
                        "date": {"type": "string", "description": "Date requested"},
                        "time": {"type": "string", "description": "Time requested"},
                        "guests": {"type": "integer", "description": "Party size"}
                    },
                    "required": ["name", "phone", "date", "time", "guests"]
                }
            }
        }
    ]

    system_prompt = f"""
You are the elite AI Concierge for 'Spoon & Stable'. 
Your tone is warm, professional, sophisticated, and concise.

Knowledge Base:
{kb_content}

CRITICAL RULES:
- Keep answers concise (2 to 3 sentences maximum).
- NEVER call `book_reservation` or say a reservation is confirmed unless the user explicitly gave you ALL 5 pieces of information: Name, Phone Number, Date, Time, and Party Size.
- If ANY detail is missing (e.g. phone number), kindly ask the user to provide the missing item before confirming.
- Note policy: The Parlour Bar does NOT accept reservations (walk-ins only).
    """

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.message}
            ],
            tools=tools,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=300
        )

        message = completion.choices[0].message

        if message.tool_calls:
            for tool_call in message.tool_calls:
                if tool_call.function.name == "book_reservation":
                    try:
                        args = json.loads(tool_call.function.arguments)
                        # Check if any field is missing or generic
                        if not args.get("phone") or args.get("phone") in ["unknown", "none", "n/a"]:
                            return {"response": f"I would be delighted to reserve a table for {args.get('name', 'you')}, but could you please provide your phone number to complete the booking?"}
                        
                        res_id = save_reservation(
                            name=args["name"],
                            phone=str(args["phone"]),
                            date=args["date"],
                            time=args["time"],
                            guests=int(args["guests"])
                        )
                        return {
                            "response": f"Thank you, {args['name']}! Your reservation for {args['guests']} guest(s) on {args['date']} at {args['time']} is confirmed. (ID: #{res_id})"
                        }
                    except Exception:
                        return {"response": "To finalize your booking, please confirm your Name, Phone Number, Date, Time, and Party Size."}

        return {"response": message.content if message.content else "How may I assist you with Spoon & Stable today?"}

    except Exception as e:
        return {"response": "I apologize, but I couldn't process that request right now. How else may I assist you with your dining plans?"}

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
    
    reservations = [dict(r) for r in rows]
    return {
        "total_reservations": len(reservations),
        "reservations": reservations
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
import os
import sys
import sqlite3
import json
from typing import List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv

# --- 0. LOAD ENVIRONMENT VARIABLES ---
# Change this line at the top of main.py:
load_dotenv(override=True)

# --- 1. DATABASE SETUP (Built-in sqlite3) ---
DB_FILE = "restaurant.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Configuration table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS restaurant_config (
            id INTEGER PRIMARY KEY,
            name TEXT,
            knowledge_base TEXT
        )
    """)
    
    # Reservations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guest_name TEXT,
            phone TEXT,
            date TEXT,
            time TEXT,
            guests INTEGER,
            status TEXT DEFAULT 'Confirmed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Seed knowledge base if empty
    cursor.execute("SELECT COUNT(*) FROM restaurant_config")
    if cursor.fetchone()[0] == 0:
        default_knowledge = """
- Location: 211 North First Street, Minneapolis, MN 55401 (North Loop neighborhood).
- Phone: 612-224-9850 | Online booking via Resy.
- Hours: Dinner served Tuesday through Sunday from 4:00 PM onwards. Closed Mondays.
- Dress Code: Smart Casual to Fine Dining attire (collared shirts/jackets for men recommended).
- Parking: Valet parking available at front entrance ($15). Nearby street and garage options.
- Walk-ins vs Reservations: Reservations open 30 days in advance via Resy. Bar and Lounge held for first-come, first-served walk-ins with full dinner menu service.
- Dietary & Allergens: Gluten-Free, Dairy-Free, Vegetarian, Vegan, and Nut-Free accommodated. Gluten-free pasta available upon request.
- Corkage Fee: $35 per 750ml bottle (Maximum 2 bottles per table).
- Large Parties: Groups larger than 8 guests require Private Dining coordination.
- Popular Dishes & Prices:
  * Bison Tartare ($24) - harissa aioli, watermelon radish, socca
  * Spaghetti Nero ($34) - octopus, prawns, mussels, fra diavolo
  * Dorothy's Pot Roast ($46) - pommes aligot, confit mushroom, rosemary broth
  * Dry-Aged Duck Breast ($48) - maple gastrique, field peas
  * Honey and Cream Cake ($14) - sweetened condensed milk ice cream
- Cocktails & Wine: Extensive Sommelier-curated wine list.
- Special Services: Private Dining room available, digital/physical Gift Cards, and signed Gavin Kaysen Cookbooks.
"""
        cursor.execute(
            "INSERT INTO restaurant_config (id, name, knowledge_base) VALUES (1, ?, ?)",
            ("Spoon and Stable", default_knowledge)
        )
        conn.commit()
    conn.close()

init_db()

# --- 2. DATABASE RESERVATION FUNCTION ---
def save_reservation_to_db(guest_name: str, phone: str, date: str, time: str, guests: int) -> str:
    """Inserts a confirmed reservation directly into SQLite."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reservations (guest_name, phone, date, time, guests) VALUES (?, ?, ?, ?, ?)",
            (guest_name, phone, date, time, guests)
        )
        conn.commit()
        res_id = cursor.lastrowid
        conn.close()
        return f"SUCCESS: Reservation #{res_id} confirmed for {guest_name} ({guests} guests) on {date} at {time}."
    except Exception as e:
        return f"ERROR: Failed to record reservation. Details: {str(e)}"

# --- 3. FASTAPI SETUP ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]

# --- 4. TOOL SCHEMA ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "book_table",
            "description": "Book a dining table reservation once guest details are collected. 'guests' MUST be passed strictly as an integer number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guest_name": {"type": "string", "description": "Full name of the guest making the booking"},
                    "phone": {"type": "string", "description": "Phone number of the guest"},
                    "date": {"type": "string", "description": "Date of reservation (e.g. YYYY-MM-DD or next Friday)"},
                    "time": {"type": "string", "description": "Time of reservation (e.g. 6:30 PM)"},
                    "guests": {
                        "type": "integer", 
                        "description": "Number of guests dining as a raw integer number (e.g. 4 or 8, NEVER inside string quotes)"
                    }
                },
                "required": ["guest_name", "phone", "date", "time", "guests"]
            }
        }
    }
]

# --- 5. CHAT ENDPOINT ---
@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    try:
        # Load the key fresh on every request & initialize Groq dynamically
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        if not groq_key:
            return {"bot_response": "⚠️ API Error: GROQ_API_KEY is missing or empty in .env"}
            
        client = Groq(api_key=groq_key)

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT name, knowledge_base FROM restaurant_config WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        
        rest_name = row[0] if row else "Spoon and Stable"
        knowledge_text = row[1] if row else ""

        system_prompt = f"""
You are the Virtual Concierge for '{rest_name}', an award-winning fine dining restaurant.

STRICT RESPONSE RULES:
- Provide medium-length responses: strictly 2 to 4 sentences maximum (or up to 3 short bullet points).
- Maintain an elegant, warm, fine-dining concierge tone.

RESERVATION GUARDRAILS:
- STANDARD BOOKINGS (1 to 8 Guests): Collect Full Name, Phone, Date, Time, and Guest Count before calling `book_table`. NEVER call `book_table` if name or phone are missing or unknown.
- LARGE PARTIES (9+ Guests): DO NOT call `book_table`. Instead, politely inform the guest that parties of 9 or more require Private Dining coordination and ask them to call 612-224-9850.
- CRITICAL: When calling `book_table`, ensure the `guests` parameter is formatted purely as an integer number.

RESTAURANT KNOWLEDGE BASE:
{knowledge_text}
"""

        messages_payload = [{"role": "system", "content": system_prompt}]
        for msg in request.messages:
            messages_payload.append({"role": msg.role, "content": msg.content})

        # Step 1: Call Groq LLaMA Model with tools enabled
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages_payload,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=250
        )

        response_message = response.choices[0].message

        # Step 2: Handle Tool Execution safely
        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                if tool_call.function.name == "book_table":
                    args = json.loads(tool_call.function.arguments)
                    
                    # Safe type conversion for 'guests' to prevent type-mismatch crashes
                    raw_guests = args.get("guests", 2)
                    try:
                        guests_num = int(raw_guests)
                    except (ValueError, TypeError):
                        guests_num = 2

                    result_str = save_reservation_to_db(
                        guest_name=str(args.get("guest_name", "Valued Guest")),
                        phone=str(args.get("phone", "N/A")),
                        date=str(args.get("date", "Today")),
                        time=str(args.get("time", "7:00 PM")),
                        guests=guests_num
                    )
                    
                    # Feed execution feedback back to LLaMA
                    messages_payload.append(response_message)
                    messages_payload.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_str
                    })
                    
                    final_response = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=messages_payload,
                        temperature=0.3,
                        max_tokens=200
                    )
                    return {"bot_response": final_response.choices[0].message.content}

        return {"bot_response": response_message.content}

    except Exception as e:
        print(f"\n[BACKEND ERROR]: {e}\n", file=sys.stderr)
        return {"bot_response": f"⚠️ API Error: {str(e)}"}

# --- 6. ADMIN DASHBOARD ENDPOINTS ---
class UpdateKnowledgeBase(BaseModel):
    new_text: str

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """Serves the Admin Portal UI."""
    try:
        with open("dashboard.html", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"<h1>Error loading dashboard.html</h1><p>{str(e)}</p>"

@app.get("/admin/get-config")
async def get_config():
    """Fetches active Knowledge Base text for the dashboard editor."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT knowledge_base FROM restaurant_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return {"knowledge_base": row[0] if row else ""}

@app.post("/admin/update-menu")
async def update_menu(data: UpdateKnowledgeBase):
    """Updates the Knowledge Base in the DB live from the dashboard."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE restaurant_config SET knowledge_base = ? WHERE id = 1", (data.new_text,))
    conn.commit()
    conn.close()
    return {"status": "success", "updated_knowledge_base": data.new_text}

@app.get("/admin/reservations")
async def get_reservations():
    """Returns all recorded bookings for the dashboard table."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, guest_name, phone, date, time, guests, status, created_at FROM reservations ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    reservations = []
    for r in rows:
        reservations.append({
            "id": r[0], "name": r[1], "phone": r[2], 
            "date": r[3], "time": r[4], "guests": r[5], 
            "status": r[6], "created_at": r[7]
        })
    return {"total_reservations": len(reservations), "reservations": reservations}
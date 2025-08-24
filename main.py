import os
import json
import uuid
from datetime import datetime
from dotenv import load_dotenv

from langchain.agents import initialize_agent, AgentType
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain.memory import ConversationBufferMemory
from langchain_core.prompts import ChatPromptTemplate

from system_prompt import SYSTEM_PROMPT

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()  # Reads from .env automatically

# -----------------------------
# LLM Initialization (done once at the start)
# -----------------------------
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

# -----------------------------
# Categories & File Path
# -----------------------------
VALID_CATEGORIES = [
    "Food", "Travel", "Shopping", "Entertainment", "Bills", "Health",
    "Stationery", "Misc", "Education", "Groceries", "Rent", "Utilities",
    "Subscriptions", "Investments", "Gifts", "Donations", "Fuel",
    "Maintenance", "Insurance", "Personal Care", "Others"
]
EXPENSE_FILE = "expenses.json"

# -----------------------------
# Helper Functions
# -----------------------------
def today_date():
    """Return today's date in YYYY-MM-DD format."""
    return datetime.today().strftime("%Y-%m-%d")

def parse_expense_with_llm(query: str):
    """Ask LLM to extract expense details in JSON format."""
    prompt = f"""
Extract the following details from the expense statement:
- category: one of {', '.join(VALID_CATEGORIES)} (if not clear, set null)
- amount (number, if not clear set 0)
- date (YYYY-MM-DD, today if not given)
- description: short phrase summarizing the expense (if not clear, set null)

⚠️ Do not guess or assign defaults like "Misc" or "General" if missing.  
Always set missing fields as null or 0.

Respond ONLY in valid JSON, no extra text.
Expense: "{query}"
Today's date: {today_date()}
"""
    resp = llm.invoke(prompt).content
    try:
        return json.loads(resp.strip().strip("`").replace("json", ""))
    except:
        return {}

# -----------------------------
# Tools
# -----------------------------
@tool
def general_query_tool(query: str) -> str:
    """
    Answer only general queries related to expenses, budgeting, or money-saving.
    If query is unrelated (e.g., politics, sports), politely redirect back.
    """
    allowed_keywords = ["expense", "spend", "budget", "save", "saving",
                        "money", "bill", "cost", "track", "finance"]
    if not any(word in query.lower() for word in allowed_keywords):
        return "⚠️ I can only help with expenses, budgeting, and money tracking. Let's focus on that 💰"

    response = llm.invoke(query)
    return getattr(response, "content", str(response))

# Global pending state
PENDING_EXPENSE = {}
CONFIRMATION_PENDING = False

@tool
def log_expense_tool(query: str) -> str:
    """
    Logs expenses with clarifying questions until all required fields are filled.
    Uses confirmation before saving into JSON.
    """
    global PENDING_EXPENSE, CONFIRMATION_PENDING

    # --- Check for confirmation step ---
    if CONFIRMATION_PENDING:
        if query.strip().lower() in ["yes", "y", "confirm", "ok", "okay", "Yes"]:
            # Save to file
            try:
                with open(EXPENSE_FILE, "r") as f:
                    expenses = json.load(f)
                if not isinstance(expenses, list):
                    expenses = [expenses]
            except (FileNotFoundError, json.JSONDecodeError):
                expenses = []

            expenses.append(PENDING_EXPENSE)
            with open(EXPENSE_FILE, "w") as f:
                json.dump(expenses, f, indent=2)

            response = f"✅ Logged: {PENDING_EXPENSE['amount']} spent on {PENDING_EXPENSE['description']} ({PENDING_EXPENSE['category']}) on {PENDING_EXPENSE['date']}."
            PENDING_EXPENSE = {}
            CONFIRMATION_PENDING = False
            return response
        else:
            # Cancel
            PENDING_EXPENSE = {}
            CONFIRMATION_PENDING = False
            return "❌ Expense logging cancelled."

    # Step 1: Parse user input
    new_data = parse_expense_with_llm(query)

    # Merge with existing pending data
    for k, v in new_data.items():
        if v:  # only update if not empty
            PENDING_EXPENSE[k] = v

    # --- Amount check ---
    try:
        PENDING_EXPENSE["amount"] = float(PENDING_EXPENSE.get("amount", 0))
    except:
        PENDING_EXPENSE["amount"] = 0

    if PENDING_EXPENSE["amount"] <= 0:
        return "❓ How much did you spend?"

    # --- Description check ---
    if not PENDING_EXPENSE.get("description"):
        return "❓ What did you spend this money on?"

    # --- Category check ---
    if not PENDING_EXPENSE.get("category") or PENDING_EXPENSE["category"] not in VALID_CATEGORIES:
        valid_list = ", ".join(VALID_CATEGORIES)
        return f"❓ Which category should I put this under? (Options: {valid_list})"

    # --- Date check ---
    if not PENDING_EXPENSE.get("date"):
        return "❓ On which date did you spend this money? (say 'today' if it was today)"

    # --- Metadata ---
    PENDING_EXPENSE["id"] = str(uuid.uuid4())
    PENDING_EXPENSE["logged_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Ask for confirmation ---
    CONFIRMATION_PENDING = True
    return f"✅ Okay, I’ll log ₹{PENDING_EXPENSE['amount']} for {PENDING_EXPENSE['description']} ({PENDING_EXPENSE['category']}) on {PENDING_EXPENSE['date']}. Confirm? (yes/no)"

@tool
def read_expenses_tool(query: str = "") -> str:
    """
    Read logged expenses from JSON.
    If a user provides a query (like 'show me food expenses this week'),
    use the LLM to interpret filters and return matching expenses.
    """
    try:
        with open(EXPENSE_FILE, "r") as f:
            expenses = json.load(f)

        if not expenses:
            return "No expenses logged yet."

        if not query.strip():
            return json.dumps(expenses, indent=2)

        # Use LLM to interpret filtering logic
        filter_prompt = ChatPromptTemplate.from_messages([
            ("system", """
                You are a filtering assistant for a personal expense tracker.
                You are given a list of expenses in JSON and a user query.
                Each expense has: amount, category, description, and date.
                Figure out which expenses match the query and return them as JSON only.
                If nothing matches, return an empty list [].
            """),
            ("user", "Expenses: {expenses}\n\nQuery: {query}")
        ])

        chain = filter_prompt | llm
        response = chain.invoke({"expenses": json.dumps(expenses), "query": query})

        return response.content.strip()

    except Exception as e:
        return f"Error while reading expenses: {str(e)}"

# -----------------------------
# Agent Setup
# -----------------------------
tools = [log_expense_tool, read_expenses_tool, general_query_tool]
memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

agent = initialize_agent(
    tools,
    llm,
    agent=AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
    verbose=True,
    handle_parsing_errors=True,
    agent_kwargs={"system_message": SYSTEM_PROMPT},
    memory=memory
)

# -----------------------------
# Chat Loop (CLI Testing)
# -----------------------------
def chat_loop():
    print("💬 Expense Manager Agent (type 'exit' to quit)")
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit", "q"]:
            print("👋 Goodbye!")
            break
        try:
            response = agent.run(user_input)
            print("Agent:", response)
        except Exception as e:
            print("⚠️ Error:", e)

if __name__ == "__main__":
    chat_loop()
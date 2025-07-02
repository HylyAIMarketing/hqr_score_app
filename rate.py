import os
import json
from notion_client import Client
from openai import OpenAI
from dotenv import load_dotenv

# --- SETUP ---
# Load environment variables for local development
load_dotenv()

# Initialize API clients from environment variables
try:
    notion = Client(auth=os.environ["NOTION_API_KEY"])
    openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    RESPONSES_DB_ID = os.environ["NOTION_RESPONSES_DB_ID"]
    # NEW: Uses a Page ID instead of a Database ID for the standards
    STANDARDS_PAGE_ID = os.environ["NOTION_STANDARDS_PAGE_ID"] 
except KeyError as e:
    print(f"Error: Missing environment variable {e}. Please ensure all required keys are set.")
    exit()

# --- CORE FUNCTIONS ---

# UPDATED: This function now reads content from a standard Notion page
def get_rating_criteria():
    """Fetches all text content from the Notion standards page."""
    print("Fetching rating criteria from Notion page...")
    try:
        # A page's content is a list of 'block' objects
        blocks = notion.blocks.children.list(block_id=STANDARDS_PAGE_ID).get("results", [])
        
        criteria_text = []
        for block in blocks:
            # Check if the block has text content (e.g., paragraph, heading, bullet)
            if 'rich_text' in block.get(block['type'], {}):
                # Extract all text from the block's rich_text array
                for text_element in block[block['type']]['rich_text']:
                    criteria_text.append(text_element['plain_text'])

        full_criteria = "\n".join(criteria_text)
        if not full_criteria.strip():
            print(f"Warning: The standards page (ID: {STANDARDS_PAGE_ID}) is empty or contains no text blocks.")
            return None
            
        print("Successfully fetched criteria from page.")
        return full_criteria
        
    except Exception as e:
        print(f"Error fetching rating criteria from page {STANDARDS_PAGE_ID}: {e}")
        return None

def get_unrated_responses():
    """Fetches all pages from the responses database with the status 'To be Rated'."""
    print("Fetching unrated responses from Notion...")
    try:
        response = notion.databases.query(
            database_id=RESPONSES_DB_ID,
            filter={"property": "Status", "select": {"equals": "To be Rated"}}
        )
        print(f"Found {len(response['results'])} responses to rate.")
        return response.get("results", [])
    except Exception as e:
        print(f"Error fetching unrated responses: {e}")
        return []

def get_rating_from_ai(criteria, user_prompt, chatbot_response):
    """Constructs the master prompt and gets a structured JSON rating from OpenAI."""
    master_prompt = f"""
You are an expert AI Quality Analyst. Your task is to evaluate a chatbot's response based on a set of predefined criteria. Provide your rating in a single, clean JSON object. Do not add any commentary, greetings, or explanations outside of the JSON structure.

# RATING CRITERIA
{criteria}

# CHATBOT INTERACTION TO EVALUATE
User Prompt: "{user_prompt}"
Chatbot Response: "{chatbot_response}"

# YOUR TASK
Analyze the "Chatbot Response" based on the "User Prompt" and the "RATING CRITERIA". Provide your evaluation as a single JSON object with keys for each score (e.g., "clarity_score", "accuracy_score") and a final key "evaluation_notes" which contains a brief justification for your ratings. The score keys in your JSON output must be in snake_case (e.g., 'clarity_score').
"""
    try:
        print("Getting rating from OpenAI...")
        response = openai.chat.completions.create(
            model="gpt-4-turbo",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are an AI Quality Analyst that only outputs valid JSON."},
                {"role": "user", "content": master_prompt}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error getting rating from OpenAI: {e}")
        return None

def update_notion_page(page_id, rating_data):
    """Updates the Notion page with the scores and justification from the AI."""
    properties_to_update = {
        "Evaluation Notes": {"rich_text": [{"text": {"content": rating_data.get("evaluation_notes", "N/A")}}]},
        "Status": {"select": {"name": "Rated"}}
    }

    # Dynamically add score properties based on what the AI returned
    for key, value in rating_data.items():
        if key.endswith("_score") and isinstance(value, (int, float)):
            # Convert snake_case (clarity_score) to Title Case (Clarity Score) for Notion property name
            prop_name = key.replace('_', ' ').title()
            properties_to_update[prop_name] = {"number": value}
    
    try:
        notion.pages.update(page_id=page_id, properties=properties_to_update)
        print(f"Successfully rated and updated page {page_id}")
    except Exception as e:
        print(f"Error updating Notion page {page_id}: {e}")
        # Update status to 'Error' for manual review
        notion.pages.update(page_id=page_id, properties={"Status": {"select": {"name": "Error"}}})

# --- MAIN EXECUTION LOGIC ---
def main():
    print("--- Starting AI Rating Script ---")
    criteria = get_rating_criteria()
    if not criteria:
        print("Could not retrieve rating criteria. Aborting.")
        return

    responses_to_rate = get_unrated_responses()
    if not responses_to_rate:
        print("No new responses to rate. Exiting.")
        return

    for page in responses_to_rate:
        page_id = page["id"]
        try:
            user_prompt = page["properties"]["Prompt"]["title"][0]["plain_text"]
            ai_response = page["properties"]["AI Response"]["rich_text"][0]["plain_text"]

            print(f"\nProcessing page: {page_id}")
            rating = get_rating_from_ai(criteria, user_prompt, ai_response)

            if rating:
                update_notion_page(page_id, rating)
            else:
                print(f"Skipping update for page {page_id} due to rating failure.")
                notion.pages.update(page_id=page_id, properties={"Status": {"select": {"name": "Error"}}})

        except (KeyError, IndexError) as e:
            print(f"Error processing page {page_id}: Missing or malformed property -> {e}. Setting status to 'Error'.")
            notion.pages.update(page_id=page_id, properties={"Status": {"select": {"name": "Error"}}})

    print("\n--- Script Finished ---")

if __name__ == "__main__":
    main()
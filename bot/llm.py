import os
import json
import aiohttp
import logging
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

logger = logging.getLogger(__name__)

API_URL = "https://api.sarvam.ai/v1/chat/completions"

# Simplified prompt so it doesn't try to echo huge document strings
SYSTEM_PROMPT = """You are a test assistant for Dukaan Saathi. 
The developer is testing the API connection.
Please reply strictly with a valid JSON object containing two keys:
1. "status": "success"
2. "message": "Live API connection successful! I received your document/text."

DO NOT wrap the JSON in markdown blocks (no ```json). Output raw JSON only.
"""

async def get_execution_plan(user_text: str) -> str:
    """
    Calls Sarvam AI asynchronously to test the live API connection safely.
    """
    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        raise ValueError("SARVAM_API_KEY environment variable is missing.")

    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "model": "sarvam-105b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ],
        "temperature": 0.1,  
        "top_p": 1,
        "max_tokens": 1024, # Increased token limit just in case
        "stream": True
    }

    accumulated_content = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, headers=headers, json=payload) as response:
                response.raise_for_status()
                
                async for line in response.content:
                    if line:
                        decoded_line = line.decode("utf-8").strip()
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]
                            
                            if data_str == "[DONE]":
                                break
                                
                            try:
                                chunk = json.loads(data_str)
                                
                                # Catch API-level errors returned in the stream
                                if "error" in chunk:
                                    logger.error(f"API Error in stream: {chunk['error']}")
                                    return f'{{"status": "error", "message": "API returned an error."}}'
                                
                                # SAFELY extract choices 
                                choices = chunk.get("choices", [])
                                if choices and len(choices) > 0:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        accumulated_content.append(content)
                                        
                            except json.JSONDecodeError:
                                logger.error(f"Failed to parse chunk: {data_str}")
                                continue
                            except Exception as e:
                                logger.error(f"Unexpected error processing chunk: {e}")
                                continue

        # Join the text and strip out any accidental markdown backticks the LLM might have added
        final_text = "".join(accumulated_content).strip()
        final_text = final_text.replace("```json", "").replace("```", "").strip()
        
        return final_text

    except Exception as e:
        logger.exception("Error communicating with Sarvam AI.")
        raise e
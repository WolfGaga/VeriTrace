import os
from typing import Optional, Dict, Any
from pathlib import Path
from google import genai
from google.genai import types
from openai import OpenAI
import requests
import logging

# Load .env file from VERITRACE (Windows adaptation: env vars not set by shell)
VERITRACE_ENV = Path(__file__).parent.parent.parent / "VERITRACE" / ".env"
if VERITRACE_ENV.exists():
    with open(VERITRACE_ENV, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


logging.getLogger('google').setLevel(logging.WARNING)
logging.getLogger('google.genai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# Read API keys from environment variables
API_KEY = os.environ.get("GEMINI_API_KEY", "")
READ_API_KEY = os.environ.get("JINA_API_KEY", "")
FACT_Model = os.environ.get("FACT_MODEL", "gemini-2.5-flash")
Model = os.environ.get("RACE_MODEL", "gemini-2.5-pro")

class AIClient:

    def __init__(self, api_key=API_KEY, model=Model):
        # Priority 1: OpenAI-compatible Gemini endpoint (e.g. tcqnz)
        base_url = os.environ.get("GEMINI_BASE_URL")
        # Priority 2: Vertex AI (paid)
        vertex_project = os.environ.get("VERTEX_AI_PROJECT")
        vertex_location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
        google_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

        if base_url:
            self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("GEMINI_BASE_URL set but GEMINI_API_KEY missing.")
            self.client = OpenAI(base_url=base_url, api_key=self.api_key, timeout=600)
            self._mode = "openai"
            logger.info(f"Using OpenAI-compatible Gemini endpoint (base_url={base_url})")
        elif vertex_project and google_creds:
            self.client = genai.Client(
                vertexai=True,
                project=vertex_project,
                location=vertex_location,
                http_options={'timeout': 600000},
            )
            self._mode = "vertex"
            logger.info(f"Using Vertex AI (project={vertex_project})")
        else:
            self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not self.api_key:
                raise ValueError("Gemini API key not provided! Please set GEMINI_API_KEY or VERTEX_AI_PROJECT.")
            self.client = genai.Client(api_key=self.api_key, http_options={'timeout': 600000})
            self._mode = "genai"

        self.model = model
        
    def generate(self, user_prompt: str, system_prompt: str = "", model: Optional[str] = None) -> str:
        """
        Generate text response
        """
        model_to_use = model or self.model
        
        # Build request content
        contents = []
        
        # Add system prompt
        if system_prompt:
            contents.append({
                "role": "system",
                "parts": [{"text": system_prompt}]
            })
        
        # Add user prompt
        contents.append({
            "role": "user", 
            "parts": [{"text": user_prompt}]
        })
        
        try:
            if self._mode == "openai":
                messages = []
                for c in contents:
                    role = c.get("role", "user")
                    text = "".join(p.get("text", "") for p in c.get("parts", []))
                    # OpenAI uses "system" / "user" / "assistant"
                    messages.append({"role": role, "content": text})
                response = self.client.chat.completions.create(
                    model=model_to_use,
                    messages=messages,
                    max_tokens=65536,
                )
                return response.choices[0].message.content

            response = self.client.models.generate_content(
                model=model_to_use,
                contents=contents,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=16000)
                )
            )

            return response.text

        except Exception as e:
            raise Exception(f"Failed to generate content: {str(e)}")

class WebScrapingJinaTool:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("JINA_API_KEY")
        if not self.api_key:
            raise ValueError("Jina API key not provided! Please set JINA_API_KEY environment variable.")

    def __call__(self, url: str) -> Dict[str, Any]:
        try:
            jina_url = f'https://r.jina.ai/{url}'
            headers = {
                "Accept": "application/json",
                'Authorization': self.api_key,
                'X-Timeout': "60000",
                "X-With-Generated-Alt": "true",
            }
            response = requests.get(jina_url, headers=headers)

            if response.status_code != 200:
                raise Exception(f"Jina AI Reader Failed for {url}: {response.status_code}")

            response_dict = response.json()

            return {
                'url': response_dict['data']['url'],
                'title': response_dict['data']['title'],
                'description': response_dict['data']['description'],
                'content': response_dict['data']['content'],
                'publish_time': response_dict['data'].get('publishedTime', 'unknown')
            }

        except Exception as e:
            logger.error(str(e))
            return {
                'url': url,
                'content': '',
                'error': str(e)
            }
        
# Lazy initialization - avoid crash on import if JINA_API_KEY not yet set
jina_tool = None

def scrape_url(url: str) -> Dict[str, Any]:
    global jina_tool
    if jina_tool is None:
        jina_tool = WebScrapingJinaTool()
    return jina_tool(url)
    
def call_model(user_prompt: str, model: str = None) -> str:
    """Call Gemini with retry for transient connection / 5xx errors."""
    import time
    client = AIClient(model=model or FACT_Model)
    last_err = None
    for attempt in range(4):
        try:
            return client.generate(user_prompt)
        except Exception as e:
            msg = str(e)
            transient = any(k in msg.lower() for k in
                            ['connection error', 'getaddrinfo', 'timeout', '503', '524', 'too many requests', '500'])
            if not transient:
                raise
            last_err = e
            wait = min(2 ** attempt, 10)
            print(f"[call_model] transient error (attempt {attempt+1}/4): {msg[:120]}; retry in {wait}s")
            time.sleep(wait)
    raise last_err if last_err else Exception("call_model: all retries failed")

if __name__ == "__main__":
    url = ""
    result = scrape_url(url)
    print(result)

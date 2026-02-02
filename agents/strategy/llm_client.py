
import os
import logging
from typing import Generator, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI, APIConnectionError, RateLimitError
except ImportError:
    OpenAI = None
    logger.warning("openai package not found. LLMClient will not work.")

class StrategyLLMClient:
    """
    Independent LLM Client for Strategic Agent.
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        if OpenAI is None:
            raise ImportError("openai package is required")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model
        self.max_tokens = 32000 # Increased for strategic planning

    def chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.6) -> str:
        """Non-streaming chat completion"""
        try:
            # For strategy, we might want default reasoning effort, or "medium" if supported.
            # Doubao supports reasoning_effort? 
            # Combat agent used "minimal". Strategy might benefit from standard.
            # We'll omit extra_body to use default behavior (usually standard/medium).
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=self.max_tokens,
                extra_body={
                    "reasoning_effort": "minimal",
                    "thinking": { "type": "disabled" }
                }
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Strategy LLM Request failed: {e}")
            raise

    def chat_stream(self, messages: List[Dict[str, str]], temperature: float = 0.6) -> Generator[str, None, None]:
        """
        Streaming chat completion.
        Yields content chunks (strings).
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=self.max_tokens,
                stream=True,
                extra_body={
                    "reasoning_effort": "minimal",
                    "thinking": { "type": "disabled" }
                }
            )
            
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"Strategy LLM Stream failed: {e}")
            raise

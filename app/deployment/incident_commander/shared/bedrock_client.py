"""
AWS Bedrock Client for Amazon Nova Pro v1

This module provides the integration with AWS Bedrock to invoke the
Amazon Nova Pro v1 model for LLM-powered reasoning and analysis.

Features:
- Boto3 client setup with proper authentication
- Credential management via environment variables
- Nova Pro v1 model invocation with retry logic
- Response parsing with structured output handling
- Token usage tracking for cost management

Usage:
    client = BedrockClient()
    response = await client.invoke(
        prompt="Analyze this log entry...",
        system_prompt="You are an incident investigator..."
    )
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class BedrockConfig:
    """
    Configuration for AWS Bedrock client.
    
    Attributes:
        region: AWS region for Bedrock service.
        model_id: Bedrock model identifier.
        access_key: AWS access key ID.
        secret_key: AWS secret access key.
        session_token: Optional session token for temporary credentials.
        max_retries: Maximum retry attempts for API calls.
        timeout: Request timeout in seconds.
    """
    region: str = "us-east-1"
    model_id: str = "amazon.nova-pro-v1:0"
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    session_token: Optional[str] = None
    max_retries: int = 3
    timeout: int = 60
    max_tokens: int = 4096
    temperature: float = 0.3
    top_p: float = 0.9


@dataclass
class InvokeResult:
    """
    Result from Bedrock model invocation.
    
    Attributes:
        content: The generated text response.
        input_tokens: Number of input tokens processed.
        output_tokens: Number of output tokens generated.
        stop_reason: Reason for generation stop.
        raw_response: Full API response for debugging.
    """
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw_response: Optional[dict[str, Any]] = None


class BedrockClientError(Exception):
    """Exception raised for Bedrock client errors."""
    pass


class BedrockClient:
    """
    AWS Bedrock client for Nova Pro v1 model invocation.
    
    This client handles all interactions with AWS Bedrock, including:
    - Authentication and session management
    - Request formatting for Nova Pro v1
    - Response parsing and error handling
    - Retry logic for transient failures
    
    Example:
        client = BedrockClient()
        result = await client.invoke(
            prompt="What caused this error?",
            system_prompt="You are an expert incident investigator."
        )
        print(result.content)
    """
    
    def __init__(self, config: Optional[BedrockConfig] = None):
        """
        Initialize the Bedrock client.
        
        Args:
            config: Optional configuration. If not provided, loads from
                   environment variables.
        """
        self.config = config or self._load_config_from_env()
        self._client = None
        self._runtime_client = None
        
        logger.info(
            f"BedrockClient initialized with model {self.config.model_id} "
            f"in region {self.config.region}"
        )
    
    def _load_config_from_env(self) -> BedrockConfig:
        """
        Load Bedrock configuration from environment variables.
        
        Environment Variables:
            AWS_ACCESS_KEY_ID: AWS access key
            AWS_SECRET_ACCESS_KEY: AWS secret key
            AWS_SESSION_TOKEN: Optional session token
            AWS_REGION: AWS region (default: us-east-1)
            BEDROCK_MODEL_ID: Model identifier
            BEDROCK_MAX_TOKENS: Maximum tokens to generate
            BEDROCK_TEMPERATURE: Sampling temperature
        
        Returns:
            BedrockConfig populated from environment.
        """
        return BedrockConfig(
            region=os.environ.get("AWS_REGION", "us-east-1"),
            model_id=os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0"),
            access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            session_token=os.environ.get("AWS_SESSION_TOKEN"),
            max_retries=int(os.environ.get("BEDROCK_MAX_RETRIES", "3")),
            timeout=int(os.environ.get("BEDROCK_TIMEOUT", "60")),
            max_tokens=int(os.environ.get("BEDROCK_MAX_TOKENS", "4096")),
            temperature=float(os.environ.get("BEDROCK_TEMPERATURE", "0.3")),
            top_p=float(os.environ.get("BEDROCK_TOP_P", "0.9")),
        )
    
    @property
    def runtime_client(self):
        """
        Get or create the Bedrock Runtime client.
        
        The runtime client is used for model invocation (inference).
        Lazily initialized on first access.
        
        Returns:
            boto3 bedrock-runtime client.
        """
        if self._runtime_client is None:
            boto_config = Config(
                retries={"max_attempts": self.config.max_retries, "mode": "adaptive"},
                read_timeout=self.config.timeout,
                connect_timeout=self.config.timeout,
            )
            
            # Build client kwargs
            client_kwargs = {
                "service_name": "bedrock-runtime",
                "region_name": self.config.region,
                "config": boto_config,
            }
            
            # Add explicit credentials if provided
            if self.config.access_key and self.config.secret_key:
                client_kwargs["aws_access_key_id"] = self.config.access_key
                client_kwargs["aws_secret_access_key"] = self.config.secret_key
                if self.config.session_token:
                    client_kwargs["aws_session_token"] = self.config.session_token
            
            self._runtime_client = boto3.client(**client_kwargs)
            logger.debug("Bedrock runtime client created successfully")
        
        return self._runtime_client
    
    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stop_sequences: Optional[list[str]] = None,
    ) -> InvokeResult:
        """
        Invoke the Nova Pro v1 model with the given prompt.
        
        This method sends a request to Bedrock and parses the response.
        For Nova Pro v1, we use the Converse API format.
        
        Args:
            prompt: The user prompt to send to the model.
            system_prompt: Optional system prompt for context.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            stop_sequences: Optional list of stop sequences.
            
        Returns:
            InvokeResult with generated content and metadata.
            
        Raises:
            BedrockClientError: If the invocation fails.
        """
        logger.debug(f"Invoking Bedrock model with prompt length: {len(prompt)}")
        
        try:
            # Build the request body for Nova Pro v1 using Converse API format
            messages = [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
            
            # Build inference configuration
            inference_config = {
                "maxTokens": max_tokens or self.config.max_tokens,
                "temperature": temperature or self.config.temperature,
                "topP": self.config.top_p,
            }
            
            if stop_sequences:
                inference_config["stopSequences"] = stop_sequences
            
            # Build the request
            request_params = {
                "modelId": self.config.model_id,
                "messages": messages,
                "inferenceConfig": inference_config,
            }
            
            # Add system prompt if provided
            if system_prompt:
                request_params["system"] = [{"text": system_prompt}]
            
            # Invoke the model using Converse API
            response = self.runtime_client.converse(**request_params)
            
            # Parse the response
            output = response.get("output", {})
            message = output.get("message", {})
            content_blocks = message.get("content", [])
            
            # Extract text content
            content = ""
            for block in content_blocks:
                if "text" in block:
                    content += block["text"]
            
            # Extract usage metrics
            usage = response.get("usage", {})
            
            result = InvokeResult(
                content=content,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                stop_reason=response.get("stopReason", ""),
                raw_response=response,
            )
            
            logger.debug(
                f"Bedrock invocation successful. "
                f"Input tokens: {result.input_tokens}, "
                f"Output tokens: {result.output_tokens}"
            )
            
            return result
            
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"Bedrock ClientError: {error_code} - {error_message}")
            raise BedrockClientError(
                f"Bedrock invocation failed: {error_code} - {error_message}"
            ) from e
            
        except Exception as e:
            logger.error(f"Unexpected error during Bedrock invocation: {e}")
            raise BedrockClientError(f"Unexpected error: {str(e)}") from e
    
    async def invoke_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stop_sequences: Optional[list[str]] = None,
    ) -> InvokeResult:
        """
        Async wrapper for invoke method.
        
        Note: boto3 is synchronous, so this runs the synchronous
        invoke in the current thread. For true async, consider
        using aioboto3 or running in a thread pool.
        
        Args:
            prompt: The user prompt to send to the model.
            system_prompt: Optional system prompt for context.
            max_tokens: Override default max tokens.
            temperature: Override default temperature.
            stop_sequences: Optional list of stop sequences.
            
        Returns:
            InvokeResult with generated content and metadata.
        """
        import asyncio
        
        # Run synchronous invoke in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.invoke(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop_sequences=stop_sequences,
            ),
        )
    
    def invoke_with_json_output(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Invoke the model and parse response as JSON.
        
        Useful for structured outputs like investigation plans
        or agent routing decisions.
        
        Args:
            prompt: The user prompt to send.
            system_prompt: Optional system prompt.
            max_tokens: Override default max tokens.
            
        Returns:
            Parsed JSON response as dictionary.
            
        Raises:
            BedrockClientError: If invocation or JSON parsing fails.
        """
        # Add JSON instruction to system prompt
        json_system = (system_prompt or "") + (
            "\n\nIMPORTANT: Respond ONLY with valid JSON. "
            "Do not include any text before or after the JSON object."
        )
        
        result = self.invoke(
            prompt=prompt,
            system_prompt=json_system,
            max_tokens=max_tokens,
        )
        
        try:
            # Try to extract JSON from response
            content = result.content.strip()
            
            # Handle markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            
            return json.loads(content.strip())
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {result.content}")
            raise BedrockClientError(f"Invalid JSON response: {e}") from e
    
    def health_check(self) -> bool:
        """
        Verify the Bedrock client is properly configured.
        
        Sends a minimal request to verify connectivity and
        authentication.
        
        Returns:
            True if the client is healthy, False otherwise.
        """
        try:
            result = self.invoke(
                prompt="Respond with exactly: OK",
                max_tokens=10,
            )
            return "OK" in result.content
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

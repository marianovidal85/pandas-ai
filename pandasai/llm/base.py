""" Base class to implement a new LLM. """

import ast
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import openai
import requests

from ..constants import END_CODE_TAG, START_CODE_TAG
from ..exceptions import (
    APIKeyNotFoundError,
    MethodNotImplementedError,
    NoCodeFoundError,
)
from ..prompts.base import Prompt


class LLM:
    """Base class to implement a new LLM."""

    last_prompt: Optional[str] = None

    @property
    def type(self) -> str:
        """
        Return type of LLM.

        Raises:
            APIKeyNotFoundError: Type has not been implemented

        Returns:
            str: Type of LLM a string
        """
        raise APIKeyNotFoundError("Type has not been implemented")

    def _polish_code(self, code: str) -> str:
        """
        Polish the code by removing the leading "python" or "py",  \
        removing the imports and removing trailing spaces and new lines.

        Args:
            code (str): Code

        Returns:
            str: Polished code
        """
        if re.match(r"^(python|py)", code):
            code = re.sub(r"^(python|py)", "", code)
        if re.match(r"^`.*`$", code):
            code = re.sub(r"^`(.*)`$", r"\1", code)
        code = code.strip()
        return code

    def _is_python_code(self, string):
        try:
            ast.parse(string)
            return True
        except SyntaxError:
            return False

    def _extract_code(self, response: str, separator: str = "```") -> str:
        """
        Extract the code from the response.

        Args:
            response (str): Response
            separator (str, optional): Separator. Defaults to "```".

        Raises:
            NoCodeFoundError: No code found in the response

        Returns:
            str: Extracted code from the response
        """
        code = response
        match = re.search(
            rf"{START_CODE_TAG}(.*)({END_CODE_TAG}|{END_CODE_TAG.replace('<', '</')})",
            code,
            re.DOTALL,
        )
        if match:
            code = match.group(1).strip()
        if len(code.split(separator)) > 1:
            code = code.split(separator)[1]
        code = self._polish_code(code)
        if not self._is_python_code(code):
            raise NoCodeFoundError("No code found in the response")

        return code

    @abstractmethod
    def call(self, instruction: Prompt, value: str, suffix: str = "") -> str:
        """
        Execute the LLM with given prompt.

        Args:
            instruction (Prompt): Prompt
            value (str): Value
            suffix (str, optional): Suffix. Defaults to "".

        Raises:
            MethodNotImplementedError: Call method has not been implemented
        """
        raise MethodNotImplementedError("Call method has not been implemented")

    def generate_code(self, instruction: Prompt, prompt: str) -> str:
        """
        Generate the code based on the instruction and the given prompt.

        Returns:
            str: Code
        """
        return self._extract_code(self.call(instruction, prompt, suffix="\n\nCode:\n"))


class BaseOpenAI(LLM, ABC):
    """Base class to implement a new OpenAI LLM"""

    api_token: str
    temperature: float = 0
    max_tokens: int = 512
    top_p: float = 1
    frequency_penalty: float = 0
    presence_penalty: float = 0.6
    stop: Optional[str] = None

    def _set_params(self, **kwargs):
        valid_params = [
            "model",
            "engine",
            "deployment_id",
            "temperature",
            "max_tokens",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "stop",
        ]
        for key, value in kwargs.items():
            if key in valid_params:
                setattr(self, key, value)

    @property
    def _default_params(self) -> Dict[str, Any]:
        """Get the default parameters for calling OpenAI API"""
        return {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
        }

    def completion(self, prompt: str) -> str:
        """
        Query the completion API

        Args:
            prompt (str): Prompt

        Returns:
            str: LLM response
        """
        params = {**self._default_params, "prompt": prompt}

        if self.stop is not None:
            params["stop"] = [self.stop]

        response = openai.Completion.create(**params)

        return response["choices"][0]["text"]

    def chat_completion(self, value: str) -> str:
        """
        Query the chat completion API

        Args:
            value (str): Prompt

        Returns:
            str: LLM response
        """
        params = {
            **self._default_params,
            "messages": [
                {
                    "role": "system",
                    "content": value,
                }
            ],
        }

        if self.stop is not None:
            params["stop"] = [self.stop]

        response = openai.ChatCompletion.create(**params)

        return response["choices"][0]["message"]["content"]


class HuggingFaceLLM(LLM):
    """Base class to implement a new Hugging Face LLM."""

    last_prompt: Optional[str] = None
    api_token: str
    _api_url: str = "https://api-inference.huggingface.co/models/"
    _max_retries: int = 3

    @property
    def type(self) -> str:
        return "huggingface-llm"

    def query(self, payload):
        """Query the API"""

        headers = {"Authorization": f"Bearer {self.api_token}"}

        response = requests.post(
            self._api_url, headers=headers, json=payload, timeout=60
        )

        return response.json()[0]["generated_text"]

    def call(self, instruction: Prompt, value: str, suffix: str = "") -> str:
        """Call the LLM"""

        payload = instruction + value + suffix

        # sometimes the API doesn't return a valid response, so we retry passing the
        # output generated from the previous call as the input
        for _i in range(self._max_retries):
            response = self.query({"inputs": payload})
            payload = response
            if response.count("<endCode>") >= 2:
                break

        # replace instruction + value from the inputs to avoid showing it in the output
        output = response.replace(instruction + value + suffix, "")
        return output


class BaseGoogle(LLM):
    """Base class to implement a new Google LLM"""

    genai: Any
    temperature: Optional[float] = 0
    top_p: Optional[float] = None
    top_k: Optional[float] = None
    max_output_tokens: Optional[int] = None

    def _configure(self, api_key: str):
        if not api_key:
            raise APIKeyNotFoundError("Google Palm API key is required")
        try:
            # pylint: disable=import-outside-toplevel
            import google.generativeai as genai

            genai.configure(api_key=api_key)
        except ImportError as ex:
            raise ImportError(
                "Could not import google-generativeai python package. "
                "Please install it with `pip install google-generativeai`."
            ) from ex
        self.genai = genai

    def _valid_params(self):
        return ["temperature", "top_p", "top_k", "max_output_tokens"]

    def _set_params(self, **kwargs):
        valid_params = self._valid_params()
        for key, value in kwargs.items():
            if key in valid_params:
                setattr(self, key, value)

    def _validate(self):
        """Validates the parameters for Google"""

        if self.temperature is not None and not 0 <= self.temperature <= 1:
            raise ValueError("temperature must be in the range [0.0, 1.0]")

        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p must be in the range [0.0, 1.0]")

        if self.top_k is not None and not 0 <= self.top_k <= 1:
            raise ValueError("top_k must be in the range [0.0, 1.0]")

        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")

    @abstractmethod
    def _generate_text(self, prompt: str) -> str:
        """
        Generates text for prompt, specific to implementation.

        Args:
            prompt (str): Prompt

        Returns:
            str: LLM response
        """
        raise MethodNotImplementedError("method has not been implemented")

    def call(self, instruction: Prompt, value: str, suffix: str = "") -> str:
        """
        Call the Google LLM.

        Args:
            instruction (str): Instruction to pass
            value (str): Value to pass
            suffix (str): Suffix to pass

        Returns:
            str: Response
        """
        self.last_prompt = str(instruction) + str(value)
        prompt = str(instruction) + str(value) + suffix
        return self._generate_text(prompt)

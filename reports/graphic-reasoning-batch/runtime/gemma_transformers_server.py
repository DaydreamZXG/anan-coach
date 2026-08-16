#!/usr/bin/env python3
"""Small localhost-only OpenAI-compatible server for Gemma 4 image analysis.

This is a benchmark runtime, not a production service.  It intentionally keeps
one inference in flight so two large requests cannot exhaust both GPUs.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from transformers import AutoModelForMultimodalLM, AutoProcessor, StoppingCriteria, StoppingCriteriaList


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[dict[str, Any]]
    max_tokens: int = Field(default=2048, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0)


class StopAfterTopLevelJson(StoppingCriteria):
    """Stop as soon as the first top-level JSON object closes."""

    def __init__(self, processor: Any, prompt_tokens: int) -> None:
        self.processor = processor
        self.last_length = prompt_tokens
        self.started = False
        self.depth = 0
        self.in_string = False
        self.escaped = False

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
        new_ids = input_ids[0, self.last_length :].tolist()
        self.last_length = int(input_ids.shape[-1])
        text = self.processor.decode(new_ids, skip_special_tokens=True)
        for character in text:
            if not self.started:
                if character == "{":
                    self.started = True
                    self.depth = 1
                continue
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif character == "\\":
                    self.escaped = True
                elif character == '"':
                    self.in_string = False
                continue
            if character == '"':
                self.in_string = True
            elif character == "{":
                self.depth += 1
            elif character == "}":
                self.depth -= 1
                if self.depth == 0:
                    return True
        return False


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI image_url items to Transformers multimodal chat items."""
    normalized: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if isinstance(content, str):
            items = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            items = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    items.append({"type": "text", "text": str(item.get("text") or "")})
                elif item_type == "image_url":
                    image_url = item.get("image_url")
                    url = image_url.get("url") if isinstance(image_url, dict) else image_url
                    if not isinstance(url, str) or not url:
                        raise ValueError("image_url item is missing a URL")
                    items.append({"type": "image", "url": url})
                elif item_type == "image":
                    copied = dict(item)
                    copied["type"] = "image"
                    items.append(copied)
        else:
            raise ValueError("message content must be a string or an item list")
        normalized.append({"role": role, "content": items})
    return normalized


def build_app(model_dir: str, served_model_name: str) -> FastAPI:
    app = FastAPI(title="Gemma 4 benchmark runtime")
    processor: Any = None
    model: Any = None
    inference_lock = asyncio.Lock()

    @app.on_event("startup")
    async def load_model() -> None:
        nonlocal processor, model
        torch.backends.cuda.matmul.allow_tf32 = True
        processor = AutoProcessor.from_pretrained(model_dir, padding_side="left")
        model = AutoModelForMultimodalLM.from_pretrained(
            model_dir,
            dtype=torch.bfloat16,
            device_map="auto",
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
            max_memory={0: "45GiB", 1: "45GiB", "cpu": "180GiB"},
        )
        model.eval()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ready" if model is not None else "loading",
            "model": served_model_name,
            "cuda_devices": torch.cuda.device_count(),
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": served_model_name, "object": "model", "owned_by": "local"}],
        }

    def generate(request: ChatRequest) -> tuple[str, int, int]:
        if model is None or processor is None:
            raise RuntimeError("model is not loaded")
        messages = normalize_messages(request.messages)
        inputs = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            # Structured extraction needs a JSON-only answer. The model's chat
            # template otherwise emits a separate thought channel before it.
            enable_thinking=False,
        ).to(model.device, dtype=model.dtype)
        input_tokens = int(inputs["input_ids"].shape[-1])
        generation_args: dict[str, Any] = {
            "max_new_tokens": request.max_tokens,
            "do_sample": request.temperature > 0,
            "use_cache": True,
            "stopping_criteria": StoppingCriteriaList([StopAfterTopLevelJson(processor, input_tokens)]),
        }
        if request.temperature > 0:
            generation_args["temperature"] = request.temperature
        with torch.inference_mode():
            output_ids = model.generate(**inputs, **generation_args)
        generated = output_ids[0][input_tokens:]
        text = processor.decode(generated, skip_special_tokens=True).strip()
        return text, input_tokens, int(generated.shape[-1])

    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatRequest) -> dict[str, Any]:
        if request.model not in (None, served_model_name):
            raise HTTPException(status_code=404, detail=f"unknown model: {request.model}")
        try:
            async with inference_lock:
                content, prompt_tokens, completion_tokens = await asyncio.to_thread(generate, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": served_model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--served-model-name", default="gemma-4-31b-it")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        build_app(args.model_dir, args.served_model_name),
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()

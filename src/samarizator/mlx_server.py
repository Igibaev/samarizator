"""Summary model on Apple's MLX, behind the same loopback API as llama-server.

Experimental second engine: on Apple Silicon MLX generates text noticeably faster than
llama.cpp for the same model. It runs as a child process of the worker — the same
frozen app started as `Samarizator --run-module samarizator.mlx_server` — and answers
the subset of llama-server's API the summary pipeline uses:

    GET  /health                → 200 once the model is loaded
    POST /tokenize              → {"tokens": [...]}
    POST /v1/chat/completions   → OpenAI-style answer with finish_reason stop/length

JSON answers follow the same GBNF grammar as with llama.cpp (llguidance computes the
allowed tokens at every step). The common beginning of consecutive prompts stays in the
KV cache, so the system prompt and the shared part of a block are not read again.
One request at a time: MLX keeps one model and one cache. Loopback only, one-time key.
"""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np


class Engine:
    """Model, tokenizer and the cache of the previous prompt."""

    def __init__(self, model_dir, context):
        from mlx_lm import load

        self.model, self.tokenizer = load(str(model_dir))
        self.context = context
        self.lock = threading.Lock()
        self.cache = None
        self.cached = []  # tokens whose keys and values are in self.cache
        self.lltokenizer = None

    # -- tokens ----------------------------------------------------------------------

    def tokenize(self, text):
        return list(self.tokenizer.encode(text, add_special_tokens=False))

    def prompt_tokens(self, messages, template_kwargs):
        return list(
            self.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=True, **(template_kwargs or {})
            )
        )

    # -- grammar ---------------------------------------------------------------------

    def matcher(self, grammar):
        import llguidance
        import llguidance.hf

        if self.lltokenizer is None:
            self.lltokenizer = llguidance.hf.from_tokenizer(
                self.tokenizer._tokenizer, eos_token=sorted(self.tokenizer.eos_token_ids)
            )
        matcher = llguidance.LLMatcher(self.lltokenizer, llguidance.grammar_from("gbnf", grammar))
        if matcher.is_error():
            raise ValueError("grammar: " + matcher.get_error())
        return matcher

    def constrain(self, matcher, fed):
        """A logits processor that allows only tokens the grammar accepts next."""
        import llguidance.numpy
        import mlx.core as mx

        vocab = self.lltokenizer.vocab_size
        bitmask = llguidance.numpy.allocate_token_bitmask(1, vocab)
        consumed = [0]

        def process(tokens, logits):
            generated = tokens.tolist()[fed:]
            for token in generated[consumed[0] :]:
                matcher.consume_token(int(token))
            consumed[0] = len(generated)
            llguidance.numpy.fill_next_token_bitmask(matcher, bitmask)
            allowed = np.unpackbits(bitmask.view(np.uint8), bitorder="little")[:vocab].astype(bool)
            width = logits.shape[-1]
            if width > vocab:  # padded embedding rows are never valid tokens
                allowed = np.concatenate([allowed, np.zeros(width - vocab, dtype=bool)])
            return mx.where(mx.array(allowed[:width])[None, :], logits, -mx.inf)

        return process

    # -- generation ------------------------------------------------------------------

    def reuse(self, tokens):
        """Keep the cached common beginning; return the tokens still to be read."""
        from mlx_lm.models.cache import can_trim_prompt_cache, make_prompt_cache, trim_prompt_cache

        common = 0
        if self.cache is not None and can_trim_prompt_cache(self.cache):
            limit = min(len(self.cached), len(tokens) - 1)  # at least one token is read anew
            while common < limit and self.cached[common] == tokens[common]:
                common += 1
            trim_prompt_cache(self.cache, len(self.cached) - common)
        else:
            self.cache = make_prompt_cache(self.model)
        if common == 0:
            self.cache = make_prompt_cache(self.model)
        self.cached = tokens[:common]
        return tokens[common:], common

    def complete(self, body):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        tokens = self.prompt_tokens(body["messages"], body.get("chat_template_kwargs"))
        max_tokens = int(body.get("max_tokens") or 512)
        if len(tokens) + max_tokens > self.context:
            return 400, {"error": {"message": "the request exceeds the available context size"}}
        with self.lock:
            fresh, reused = self.reuse(tokens)
            processors = []
            if body.get("grammar"):
                processors.append(self.constrain(self.matcher(body["grammar"]), len(fresh)))
            sampler = make_sampler(temp=float(body.get("temperature", 0.2)), top_p=float(body.get("top_p", 0.9)))
            text, generated, finish = [], [], "length"
            for response in stream_generate(
                self.model,
                self.tokenizer,
                fresh,
                max_tokens=max_tokens,
                sampler=sampler,
                logits_processors=processors,
                prompt_cache=self.cache,
            ):
                text.append(response.text)
                generated.append(int(response.token))
                if response.finish_reason:
                    finish = response.finish_reason
            # What the cache now holds: the prompt and every generated token it has read.
            offset = getattr(self.cache[0], "offset", None) if self.cache else None
            self.cached = (tokens + generated)[:offset] if isinstance(offset, int) else []
            if not isinstance(offset, int):
                self.cache = None
        return 200, {
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": "".join(text)}}],
            "usage": {"prompt_tokens": len(tokens), "cached_tokens": reused, "completion_tokens": len(generated)},
        }


def handler_for(engine, key):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self.reply(200, {"status": "ok"})
            self.reply(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if key and self.headers.get("Authorization") != f"Bearer {key}":
                return self.reply(401, {"error": {"message": "invalid api key"}})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if self.path == "/tokenize":
                    return self.reply(200, {"tokens": engine.tokenize(str(body.get("content", "")))})
                if self.path == "/v1/chat/completions":
                    return self.reply(*engine.complete(body))
                self.reply(404, {"error": {"message": "not found"}})
            except Exception as exc:  # the worker must see an HTTP error, not a dropped socket
                self.reply(500, {"error": {"message": type(exc).__name__}})

    return Handler


def serve(model_dir, port, key, context, ready=None):
    import os

    if os.environ.get("SAMARIZATOR_MLX_DEVICE") == "cpu":
        import mlx.core as mx

        mx.set_default_device(mx.cpu)  # tests on machines without a usable GPU
    engine = Engine(model_dir, context)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(engine, key))
    if ready:
        ready(server)
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--ctx", type=int, default=32768)
    args = parser.parse_args(argv)
    try:
        serve(args.model, args.port, args.api_key, args.ctx)
    except Exception as exc:
        print(f"MLX: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

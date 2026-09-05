"""Bound retrieved evidence and keep citations aligned with the inference input."""
import hashlib
import json
import logging
import math
import os
import re
import time

from rag.prompt_builder import build_user_prompt
from utils.errors import InferenceError

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(levelname)s %(name)s %(message)s'))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
INSUFFICIENT = "Insufficient repository evidence to answer this question."


def positive_int(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def prepare_context(results, repositories):
    limit = positive_int("RAG_MAX_CONTEXT_CHARS", 3000)
    top_k = positive_int("RAG_TOP_K", 5)
    ranked = sorted(results, key=lambda r: float(r.get('score', 0)), reverse=True)
    blocks, sources, supplied, seen = [], [], [], set()
    for result in ranked:
        score = float(result.get('score', 0))
        text = result.get('text')
        # Same gate as existing hybrid retrieval; never interpret this as probability.
        if not math.isfinite(score) or score < 0.25 or not isinstance(text, str) or not text.strip():
            continue
        meta = result.get('metadata') or {}
        repository = result.get('repo_id', repositories[0])
        if repository not in repositories:
            continue
        file = meta.get('file_path')
        digest = hashlib.sha256(json.dumps([repository, file, meta.get('section'),
                                          meta.get('start_line'), meta.get('end_line'), text],
                                          ensure_ascii=False).encode('utf-8')).hexdigest()[:20]
        chunk_id = meta.get('chunk_id') or digest
        key = (repository, file, chunk_id)
        if key in seen:
            continue
        seen.add(key)
        source = {'repository': repository, 'repo_id': repository, 'file': file,
                  'chunk_id': chunk_id, 'section': meta.get('section'), 'score': score}
        # Older indexes have no original-source line mapping. Do not invent it.
        start, end = meta.get('start_line'), meta.get('end_line')
        if isinstance(start, int) and isinstance(end, int) and 0 < start <= end:
            source.update(start_line=start, end_line=end)
        header = '[Source ' + json.dumps(source, ensure_ascii=False) + ']\n'
        block = header + text.strip()
        remaining = limit - len('\n\n'.join(blocks)) - (2 if blocks else 0)
        if len(block) > remaining:
            if blocks:
                break  # Keep highest-ranked complete chunks first.
            suffix = '\n[excerpt truncated]'
            budget = remaining - len(header) - len(suffix)
            if budget < 1:
                break
            excerpt = text.strip()[:budget].rstrip()
            if not excerpt:
                break
            block = header + excerpt + suffix
        blocks.append(block)
        sources.append(source)
        supplied.append(result)
        if len(blocks) >= top_k:
            break
    return '\n\n'.join(blocks), sources, supplied


def answer_from_results(question, results, repositories, generate, show_confidence, confidence):
    context, sources, supplied = prepare_context(results, repositories)
    response = {'repository': repositories[0] if len(repositories) == 1 else repositories,
                'sources': sources}
    logger.info('rag evidence repositories=%s retrieved=%d supplied=%d context_chars=%d chunks=%s',
                repositories, len(results), len(sources), len(context),
                [(s['file'], s['chunk_id']) for s in sources])
    if not supplied:
        logger.info('rag outcome=insufficient_evidence')
        return dict(response, answer=INSUFFICIENT, confidence='Low')
    started = time.monotonic()
    logger.info('rag inference model=service_default configured_hint=%s', os.getenv('LLM_DEFAULT_MODEL', 'service-managed'))
    try:
        response['answer'] = generate(build_user_prompt(question, context))
        response['answer'] = _preserve_code_return_expression(question, response['answer'], supplied)
    except InferenceError as exc:
        logger.warning('rag inference outcome=%s duration=%.3f', exc.code, time.monotonic()-started)
        raise
    except Exception as exc:
        logger.warning('rag inference outcome=unexpected_failure duration=%.3f', time.monotonic()-started)
        raise InferenceError('inference_failure', 'Unexpected inference service failure', 500) from exc
    logger.info('rag inference outcome=success duration=%.3f', time.monotonic()-started)
    if show_confidence:
        response['confidence'] = confidence(supplied)
    return response


def _preserve_code_return_expression(question, answer, supplied):
    """Reject a numeric evaluation when evidence gives a symbolic return expression.

    Small local models sometimes evaluate parameterized code (``a + b`` -> ``4``).
    That value is unsupported repository evidence, so retain the exact expression.
    """
    if not re.search(r"\b(return|returns)\b", question, re.IGNORECASE):
        return answer
    names = re.findall(r"\b(?:function|def)\s+([A-Za-z_]\w*)\b", question, re.IGNORECASE)
    names += re.findall(r"\b([A-Za-z_]\w*)\s+function\b", question, re.IGNORECASE)
    for item in supplied:
        text = item.get("text", "")
        for name in names:
            match = re.search(r"\bdef\s+" + re.escape(name) + r"\s*\([^)]*\)\s*:\s*(?:\n\s*)?(?:[^\n]*\n\s*)*?return\s+([^\n#]+)", text)
            if not match:
                continue
            expression = match.group(1).strip().rstrip()
            if (not re.fullmatch(r"[0-9.]+", expression)
                    and re.search(r"\breturns?\b[^\n]*\b\d+(?:\.\d+)?\b", answer, re.IGNORECASE)):
                return f"The `{name}` function returns `{expression}`."
    return answer

import datetime
import json
import time
from uuid import uuid4

import requests
from loguru import logger

from .protocol import Paper

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
RETRYABLE_CODES = {11232, 230020}
THREAD_FALLBACK_CODES = {230071, 230072}
DEFAULT_BATCH_SIZE = 5


class FeishuAPIError(RuntimeError):
    def __init__(self, http_status: int, code: int, msg: str, body: dict):
        super().__init__(f"Feishu API error: HTTP {http_status}, code={code}, msg={msg}")
        self.http_status = http_status
        self.code = code
        self.msg = msg
        self.body = body


def _to_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _to_positive_int(value, default: int) -> int:
    try:
        val = int(value)
        return val if val > 0 else default
    except Exception:
        return default


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _build_summary(papers: list[Paper], today: str) -> str:
    if not papers:
        return f"Daily arXiv {today}\nNo new papers today."

    score_high = sum(1 for p in papers if p.score is not None and p.score >= 7.5)
    score_mid = sum(1 for p in papers if p.score is not None and 6.5 <= p.score < 7.5)
    score_low = len(papers) - score_high - score_mid
    return "\n".join(
        [
            f"Daily arXiv {today} - {len(papers)} papers",
            f"High relevance (>=7.5): {score_high}",
            f"Mid relevance (>=6.5): {score_mid}",
            f"Low relevance (<6.5): {score_low}",
        ]
    )


def _build_paper_text(paper: Paper, index: int) -> str:
    title = _truncate(paper.title.strip(), 180)
    tldr = _truncate((paper.tldr or paper.abstract or "No summary available").strip(), 600)
    score_text = f"{paper.score:.2f}" if paper.score is not None else "N/A"
    authors = _truncate(", ".join(paper.authors[:8]), 260) if paper.authors else "Unknown"
    links = []
    if paper.pdf_url:
        links.append(f"PDF: {paper.pdf_url}")
    if paper.url:
        links.append(f"Paper: {paper.url}")
    links_text = "\n".join(links) if links else "Links: N/A"
    return "\n".join(
        [
            f"{index}. {title}",
            f"Score: {score_text}",
            f"Authors: {authors}",
            f"TLDR: {tldr}",
            links_text,
        ]
    )


def _build_batch_text(batch: list[Paper], start_index: int) -> str:
    blocks = [_build_paper_text(paper, start_index + idx) for idx, paper in enumerate(batch)]
    return "\n\n".join(blocks)


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 20, max_retries: int = 3) -> dict:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            body = resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                backoff = 2**attempt
                logger.warning(f"Feishu request failed, retry in {backoff}s: {exc}")
                time.sleep(backoff)
                continue
            raise RuntimeError(f"Feishu request failed after {max_retries} retries: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"Feishu response is not valid JSON: {resp.text}") from exc

        code = int(body.get("code", 0))
        msg = str(body.get("msg", ""))
        if resp.status_code == 429 or code in RETRYABLE_CODES:
            if attempt < max_retries - 1:
                backoff = 2**attempt
                logger.warning(
                    f"Feishu rate limited/busy (http={resp.status_code}, code={code}), retry in {backoff}s"
                )
                time.sleep(backoff)
                continue
        if resp.status_code >= 500:
            if attempt < max_retries - 1:
                backoff = 2**attempt
                logger.warning(f"Feishu 5xx error, retry in {backoff}s: {body}")
                time.sleep(backoff)
                continue
        if resp.status_code >= 400 or code != 0:
            raise FeishuAPIError(resp.status_code, code, msg, body)
        return body

    if last_exc:
        raise RuntimeError(f"Feishu request failed: {last_exc}")
    raise RuntimeError("Feishu request failed: unknown error")


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}
    data = _post_json(url, headers={"Content-Type": "application/json"}, payload=payload)
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"Missing tenant_access_token in response: {data}")
    return token


def _send_text_message(token: str, chat_id: str, text: str) -> dict:
    url = f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = _post_json(url, headers=headers, payload=payload)
    return resp.get("data", {})


def _reply_text_message(token: str, message_id: str, text: str, reply_in_thread: bool) -> dict:
    url = f"{FEISHU_API_BASE}/im/v1/messages/{message_id}/reply"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "reply_in_thread": reply_in_thread,
        "uuid": str(uuid4()),
    }
    try:
        resp = _post_json(url, headers=headers, payload=payload)
        return resp.get("data", {})
    except FeishuAPIError as exc:
        if reply_in_thread and exc.code in THREAD_FALLBACK_CODES:
            logger.warning(
                f"reply_in_thread is not supported (code={exc.code}), fallback to normal reply"
            )
            payload["reply_in_thread"] = False
            payload["uuid"] = str(uuid4())
            resp = _post_json(url, headers=headers, payload=payload)
            return resp.get("data", {})
        raise


def create_topic_post(config, papers: list[Paper]) -> str:
    app_id = str(getattr(config.feishu, "app_id", "") or "")
    app_secret = str(getattr(config.feishu, "app_secret", "") or "")
    chat_id = str(getattr(config.executor, "feishu_chat_id", "") or "")
    if not app_id:
        raise ValueError("feishu.app_id is required when executor.output=feishu")
    if not app_secret:
        raise ValueError("feishu.app_secret is required when executor.output=feishu")
    if not chat_id:
        raise ValueError("executor.feishu_chat_id is required when executor.output=feishu")

    reply_in_thread = _to_bool(getattr(config.executor, "feishu_reply_in_thread", True), True)
    send_complete_marker = _to_bool(
        getattr(config.executor, "feishu_send_complete_marker", True), True
    )
    batch_size = _to_positive_int(
        getattr(config.executor, "feishu_batch_size", DEFAULT_BATCH_SIZE), DEFAULT_BATCH_SIZE
    )

    logger.info("Getting Feishu tenant_access_token...")
    token = _get_tenant_access_token(app_id, app_secret)

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    opener_text = _build_summary(papers, today)
    opener = _send_text_message(token, chat_id, opener_text)
    root_message_id = opener.get("message_id")
    if not root_message_id:
        raise RuntimeError(f"Failed to get opener message_id from Feishu response: {opener}")
    logger.info(f"Created Feishu opener message, message_id: {root_message_id}")

    if not papers:
        return root_message_id

    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        batch_text = _build_batch_text(batch, i + 1)
        time.sleep(1)
        _reply_text_message(token, root_message_id, batch_text, reply_in_thread=reply_in_thread)
        logger.debug(f"Sent Feishu batch {i // batch_size + 1}/{(len(papers) - 1) // batch_size + 1}")

    if send_complete_marker:
        marker = f"ARXIV_DAILY_COMPLETE | {today} | {len(papers)} papers"
        time.sleep(1)
        _reply_text_message(token, root_message_id, marker, reply_in_thread=reply_in_thread)
        logger.info("Sent Feishu completion marker")

    return root_message_id

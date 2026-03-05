import datetime
import math
import time

import requests
from loguru import logger

from .protocol import Paper

PAPERS_PER_MESSAGE = 5

_SCORE_COLORS = {
    "high": 0xE74C3C,
    "medium": 0xF39C12,
    "low": 0x3498DB,
}

_MAX_TITLE_LEN = 250
_MAX_DESC_LEN = 3800
_MAX_FIELD_LEN = 1000


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def get_stars_text(score: float) -> str:
    low, high = 6, 8
    if score <= low:
        return ""
    if score >= high:
        return "*" * 5
    interval = (high - low) / 10
    star_num = math.ceil((score - low) / interval)
    full = star_num // 2
    half = star_num - full * 2
    return "*" * full + ("+" if half else "")


def _format_authors(authors: list[str]) -> str:
    if len(authors) <= 5:
        return _truncate(", ".join(authors), _MAX_FIELD_LEN)
    return _truncate(", ".join(authors[:3] + ["..."] + authors[-2:]), _MAX_FIELD_LEN)


def _format_affiliations(affiliations: list[str] | None) -> str:
    if not affiliations:
        return "Unknown"
    shown = affiliations[:5]
    text = ", ".join(shown)
    if len(affiliations) > 5:
        text += ", ..."
    return _truncate(text, _MAX_FIELD_LEN)


def _score_color(score: float | None) -> int:
    if score is None:
        return _SCORE_COLORS["low"]
    if score >= 7.5:
        return _SCORE_COLORS["high"]
    if score >= 6.5:
        return _SCORE_COLORS["medium"]
    return _SCORE_COLORS["low"]


def render_paper_embed(paper: Paper, index: int) -> dict:
    stars = get_stars_text(paper.score) if paper.score is not None else ""
    links_parts = []
    if paper.pdf_url:
        links_parts.append(f"[PDF]({paper.pdf_url})")
    if paper.url:
        links_parts.append(f"[Paper]({paper.url})")

    fields = [
        {"name": "Authors", "value": _format_authors(paper.authors), "inline": False},
        {"name": "Affiliations", "value": _format_affiliations(paper.affiliations), "inline": False},
    ]
    if stars:
        fields.append({"name": "Relevance", "value": stars, "inline": True})
    if links_parts:
        fields.append(
            {
                "name": "Links",
                "value": _truncate(" | ".join(links_parts), _MAX_FIELD_LEN),
                "inline": True,
            }
        )

    description = paper.tldr or paper.abstract or "No summary available"

    return {
        "title": _truncate(f"{index}. {paper.title}", _MAX_TITLE_LEN),
        "url": paper.url,
        "description": _truncate(f"**TLDR:** {description}", _MAX_DESC_LEN),
        "color": _score_color(paper.score),
        "fields": fields,
    }


def _post_webhook(webhook_url: str, payload: dict) -> dict:
    url = webhook_url if "?" in webhook_url else webhook_url + "?wait=true"
    if "wait=true" not in url:
        url += "&wait=true"

    for _ in range(3):
        resp = requests.post(
            url,
            json=payload,
            timeout=30,
            headers={"User-Agent": "ZoteroArxivDaily/1.0"},
        )
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 2)
            logger.warning(f"Rate limited, retrying after {retry_after}s")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Discord webhook failed after 3 retries")


def create_forum_post(webhook_url: str, papers: list[Paper]) -> str:
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if not papers:
        payload = {
            "thread_name": f"Daily arXiv {today}",
            "content": "No new papers today.",
        }
        data = _post_webhook(webhook_url, payload)
        thread_id = data["channel_id"]
        logger.info(f"Created empty forum post, thread_id: {thread_id}")
        return thread_id

    score_high = sum(1 for p in papers if p.score is not None and p.score >= 7.5)
    score_mid = sum(1 for p in papers if p.score is not None and 6.5 <= p.score < 7.5)
    score_low = len(papers) - score_high - score_mid

    summary_lines = [
        f"Daily arXiv {today} - {len(papers)} papers",
        f"High relevance (>=7.5): {score_high}",
        f"Mid relevance (>=6.5): {score_mid}",
        f"Low relevance (<6.5): {score_low}",
    ]
    first_payload = {
        "thread_name": f"Daily arXiv {today}",
        "content": "\n".join(summary_lines),
    }
    data = _post_webhook(webhook_url, first_payload)
    thread_id = data["channel_id"]
    logger.info(f"Created forum post, thread_id: {thread_id}")

    batches = []
    for i in range(0, len(papers), PAPERS_PER_MESSAGE):
        batch_embeds = []
        for j, paper in enumerate(papers[i : i + PAPERS_PER_MESSAGE]):
            batch_embeds.append(render_paper_embed(paper, i + j + 1))
        batches.append(batch_embeds)

    thread_url = f"{webhook_url}?thread_id={thread_id}&wait=true"
    for idx, batch in enumerate(batches, start=1):
        time.sleep(1)
        _post_webhook(thread_url, {"embeds": batch})
        logger.debug(f"Sent batch {idx}/{len(batches)}")

    time.sleep(1)
    trigger = f"ARXIV_DAILY_COMPLETE | {today} | {len(papers)} papers"
    _post_webhook(thread_url, {"content": trigger})
    logger.info("Sent ARXIV_DAILY_COMPLETE trigger")

    return thread_id

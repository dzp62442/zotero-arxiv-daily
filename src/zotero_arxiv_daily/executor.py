import random
from datetime import datetime

from loguru import logger
from omegaconf import DictConfig
from openai import OpenAI
from pyzotero import zotero
from tqdm import tqdm

from .construct_email import render_email
from .protocol import CorpusPaper
from .reranker import get_reranker_cls
from .retriever import get_retriever_cls
from .utils import glob_match, send_email


class Executor:
    def __init__(self, config: DictConfig):
        self.config = config
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(
            api_key=config.llm.api.key,
            base_url=config.llm.api.base_url,
        )

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, "user", self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c["key"]: c for c in collections}
        corpus = zot.everything(zot.items(itemType="conferencePaper || journalArticle || preprint"))
        corpus = [c for c in corpus if c["data"]["abstractNote"] != ""]

        def get_collection_path(col_key: str) -> str:
            if p := collections[col_key]["data"]["parentCollection"]:
                return get_collection_path(p) + "/" + collections[col_key]["data"]["name"]
            return collections[col_key]["data"]["name"]

        for c in corpus:
            paths = [get_collection_path(col) for col in c["data"]["collections"]]
            c["paths"] = paths

        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [
            CorpusPaper(
                title=c["data"]["title"],
                abstract=c["data"]["abstractNote"],
                added_date=datetime.strptime(c["data"]["dateAdded"], "%Y-%m-%dT%H:%M:%SZ"),
                paths=c["paths"],
            )
            for c in corpus
        ]

    def filter_corpus(self, corpus: list[CorpusPaper]) -> list[CorpusPaper]:
        if not self.config.zotero.include_path:
            return corpus
        new_corpus = []
        logger.info(
            f"Selecting zotero papers matching include_path: {self.config.zotero.include_path}"
        )
        for c in corpus:
            match_results = [glob_match(p, self.config.zotero.include_path) for p in c.paths]
            if any(match_results):
                new_corpus.append(c)
        samples = random.sample(new_corpus, min(5, len(new_corpus)))
        samples = "\n".join([c.title + " - " + "\n".join(c.paths) for c in samples])
        logger.info(f"Selected {len(new_corpus)} zotero papers:\n{samples}\n...")
        return new_corpus

    def run(self):
        output = str(getattr(self.config.executor, "output", "email")).lower()

        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return

        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)

        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            reranked_papers = reranked_papers[: self.config.executor.max_paper_num]
            logger.info("Generating TLDR and affiliations...")
            for p in tqdm(reranked_papers):
                p.generate_tldr(self.openai_client, self.config.llm)
                p.generate_affiliations(self.openai_client, self.config.llm)
        elif output != "discord" and not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return

        if output == "email":
            logger.info("Sending email...")
            email_content = render_email(reranked_papers)
            send_email(self.config, email_content)
            logger.info("Email sent successfully")
            return

        if output == "discord":
            webhook_url = getattr(self.config.executor, "discord_webhook_url", None)
            if not webhook_url:
                raise ValueError(
                    "executor.discord_webhook_url is required when executor.output=discord"
                )
            from .construct_discord import create_forum_post

            logger.info("Posting to Discord forum...")
            thread_id = create_forum_post(webhook_url, reranked_papers)
            logger.info(f"Posted to Discord forum, thread_id: {thread_id}")
            return

        raise ValueError(
            f"Unsupported executor.output={output}. Expected 'email' or 'discord'."
        )

#!/usr/bin/env python3
"""
RAG Corpus Manager — add, remove, list, and audit RAG sources.

Usage:
    python rag/corpus_manager.py stats
    python rag/corpus_manager.py list [--asset-class gold]
    python rag/corpus_manager.py add --file path/to/paper.pdf --source-id my_paper \\
        --title "Paper Title" --authors "Author1,Author2" \\
        --authority-tier 2 --asset-class gold --collection papers
    python rag/corpus_manager.py remove --source-id erb_harvey_golden_dilemma
    python rag/corpus_manager.py backfill-authority
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml
import chromadb

_BOTDIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOTDIR))

SOURCES_YAML = _BOTDIR / "config" / "rag_sources.yaml"
CHROMADB_PATH = str(_BOTDIR / "chromadb-data")


def _load_sources() -> dict:
    with open(SOURCES_YAML) as f:
        return yaml.safe_load(f)


def _save_sources(data: dict):
    with open(SOURCES_YAML, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=120)


def _get_client():
    return chromadb.PersistentClient(path=CHROMADB_PATH)


def _chunk_text(text, max_chars=1000):
    paragraphs = text.split('\n\n')
    chunks = []
    current = ""
    for para in paragraphs:
        if len(para.strip()) < 50:
            continue
        if len(current + para) < max_chars:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n\n"
    if current:
        chunks.append(current.strip())
    return chunks


class CorpusManager:
    def __init__(self):
        self.client = _get_client()
        self.config = _load_sources()
        self.sources = self.config.get("sources", {})

    def add_source(self, file_path: str, source_id: str, title: str,
                   authors: list, authority_tier: int, asset_classes: list,
                   collection_name: str, published_date: str = "2025-01-01") -> int:
        """Chunk, embed, and ingest a new source. Returns chunk count."""
        import fitz
        path = Path(file_path)
        if not path.exists():
            print(f"File not found: {path}")
            return 0

        # Extract text
        if path.suffix.lower() == ".pdf":
            doc = fitz.open(path)
            chunks = []
            for page_num in range(len(doc)):
                text = doc.load_page(page_num).get_text()
                if text.strip():
                    for chunk in _chunk_text(text):
                        chunks.append((chunk, page_num + 1))
            doc.close()
        elif path.suffix.lower() == ".epub":
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
            book = epub.read_epub(str(path), options={"ignore_ncx": True})
            chunks = []
            ch = 0
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                ch += 1
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text = soup.get_text(separator="\n\n")
                for chunk in _chunk_text(text):
                    chunks.append((chunk, ch))
        else:
            print(f"Unsupported format: {path.suffix}")
            return 0

        if not chunks:
            print(f"No text extracted from {path.name}")
            return 0

        # Ingest into ChromaDB
        collection = self.client.get_or_create_collection(collection_name)
        author_str = ", ".join(authors)
        texts, metadatas, ids = [], [], []
        for i, (text, loc) in enumerate(chunks):
            texts.append(text)
            metadatas.append({
                "source": str(path),
                "source_id": source_id,
                "author": author_str,
                "title": title,
                "page": loc,
                "authority_tier": authority_tier,
                "asset_class": ",".join(asset_classes),
                "document_type": "paper" if collection_name == "papers" else "book",
                "published_date": published_date,
                "published_date_int": int(published_date.replace("-", "")),
            })
            ids.append(f"{source_id}_{i}")

        BATCH = 100
        for i in range(0, len(texts), BATCH):
            collection.add(
                documents=texts[i:i + BATCH],
                metadatas=metadatas[i:i + BATCH],
                ids=ids[i:i + BATCH],
            )

        # Update YAML
        self.sources[source_id] = {
            "title": title,
            "authors": authors,
            "authority_tier": authority_tier,
            "source_class": "academic_paper" if authority_tier <= 2 else "practitioner_book",
            "asset_classes": asset_classes,
            "collection": collection_name,
            "file": str(path),
            "ingested": True,
            "chunk_count": len(chunks),
        }
        self.config["sources"] = self.sources
        _save_sources(self.config)

        print(f"Added {len(chunks)} chunks from '{title}' to {collection_name}")
        return len(chunks)

    def remove_source(self, source_id: str) -> int:
        """Remove all chunks for a source from ChromaDB. Returns chunks removed."""
        if source_id not in self.sources:
            print(f"Source '{source_id}' not found in rag_sources.yaml")
            return 0

        source = self.sources[source_id]
        collection_name = source["collection"]
        collection = self.client.get_or_create_collection(collection_name)

        # Find chunks by ID prefix
        all_ids = collection.get(include=[])["ids"]
        to_remove = [id_ for id_ in all_ids if id_.startswith(f"{source_id}_")]

        if not to_remove:
            # Try matching by title in metadata
            results = collection.get(limit=10000, include=["metadatas"])
            to_remove = [
                id_ for id_, m in zip(results["ids"], results["metadatas"])
                if m.get("title") == source["title"] or m.get("source_id") == source_id
            ]

        if to_remove:
            for i in range(0, len(to_remove), 100):
                collection.delete(ids=to_remove[i:i + 100])

        # Update YAML
        self.sources[source_id]["ingested"] = False
        self.sources[source_id]["chunk_count"] = 0
        self.config["sources"] = self.sources
        _save_sources(self.config)

        print(f"Removed {len(to_remove)} chunks for '{source['title']}' from {collection_name}")
        return len(to_remove)

    def list_sources(self, asset_class: str = None) -> list:
        """List all sources, optionally filtered by asset class."""
        results = []
        for sid, src in sorted(self.sources.items()):
            if asset_class and asset_class not in src.get("asset_classes", []):
                continue
            results.append({"id": sid, **src})
        return results

    def get_corpus_stats(self) -> dict:
        """Return chunk counts per collection, per authority tier, per asset class."""
        stats = {
            "collections": {},
            "by_tier": defaultdict(int),
            "by_asset_class": defaultdict(int),
            "total_chunks": 0,
            "total_sources": len(self.sources),
        }

        for name in ["methodology", "risk_mgmt", "commodities", "papers"]:
            try:
                coll = self.client.get_collection(name)
                count = coll.count()
                stats["collections"][name] = count
                stats["total_chunks"] += count
            except Exception:
                stats["collections"][name] = 0

        for sid, src in self.sources.items():
            tier = src.get("authority_tier", 4)
            chunks = src.get("chunk_count", 0)
            stats["by_tier"][tier] += chunks
            for ac in src.get("asset_classes", ["general"]):
                stats["by_asset_class"][ac] += chunks

        return stats

    def backfill_authority(self) -> dict:
        """Backfill authority_tier + published_date metadata on existing chunks."""
        # Build lookup: title -> metadata to backfill
        title_map = {}
        author_map = {}
        for sid, src in self.sources.items():
            info = {
                "authority_tier": src.get("authority_tier", 4),
                "source_id": sid,
                "asset_class": ",".join(src.get("asset_classes", [])),
                "published_date": src.get("published_date", "2025-01-01"),
            }
            title_map[src["title"]] = info
            for author in src.get("authors", []):
                key = f"{author}|{src['title']}"
                author_map[key] = info

        updated_counts = {}
        for name in ["methodology", "risk_mgmt", "commodities", "papers"]:
            coll = self.client.get_collection(name)
            results = coll.get(limit=10000, include=["metadatas"])
            updated = 0

            for id_, meta in zip(results["ids"], results["metadatas"]):
                title = meta.get("title", "")
                match = title_map.get(title)
                if not match:
                    # Try substring match (YAML titles may be truncated)
                    for yaml_title, info in title_map.items():
                        if yaml_title in title or title in yaml_title:
                            match = info
                            break
                if not match:
                    # Try author+title combo
                    author = meta.get("author", "")
                    for a in author.split(","):
                        key = f"{a.strip()}|{title}"
                        match = author_map.get(key)
                        if match:
                            break

                if match:
                    new_meta = dict(meta)
                    changed = False
                    for field in ("authority_tier", "source_id", "asset_class"):
                        if new_meta.get(field) != match.get(field):
                            new_meta[field] = match[field]
                            changed = True
                    # Store published_date as int YYYYMMDD for ChromaDB $lte queries
                    pub_date_str = match.get("published_date", "2025-01-01")
                    pub_date_int = int(pub_date_str.replace("-", ""))
                    if new_meta.get("published_date_int") != pub_date_int:
                        new_meta["published_date"] = pub_date_str
                        new_meta["published_date_int"] = pub_date_int
                        changed = True

                    if changed:
                        coll.update(ids=[id_], metadatas=[new_meta])
                        updated += 1

            updated_counts[name] = updated
            if updated:
                print(f"  {name}: backfilled {updated} chunks")

        return updated_counts


def main():
    parser = argparse.ArgumentParser(description="RAG Corpus Manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("stats", help="Show corpus statistics")
    list_p = sub.add_parser("list", help="List sources")
    list_p.add_argument("--asset-class", help="Filter by asset class")

    add_p = sub.add_parser("add", help="Add a new source")
    add_p.add_argument("--file", required=True)
    add_p.add_argument("--source-id", required=True)
    add_p.add_argument("--title", required=True)
    add_p.add_argument("--authors", required=True, help="Comma-separated")
    add_p.add_argument("--authority-tier", type=int, required=True)
    add_p.add_argument("--asset-class", required=True, help="Comma-separated")
    add_p.add_argument("--collection", required=True)

    rm_p = sub.add_parser("remove", help="Remove a source")
    rm_p.add_argument("--source-id", required=True)

    sub.add_parser("backfill-authority", help="Backfill authority_tier on existing chunks")

    args = parser.parse_args()
    cm = CorpusManager()

    if args.command == "stats":
        stats = cm.get_corpus_stats()
        tiers = _load_sources().get("authority_tiers", {})
        print(f"\n{'='*60}")
        print(f"RAG CORPUS STATISTICS")
        print(f"{'='*60}")
        print(f"Total sources: {stats['total_sources']}")
        print(f"Total chunks:  {stats['total_chunks']}")
        print(f"\nBy collection:")
        for name, count in sorted(stats["collections"].items()):
            print(f"  {name:15} {count:5} chunks")
        print(f"\nBy authority tier:")
        for tier in sorted(stats["by_tier"]):
            label = tiers.get(tier, "")
            print(f"  Tier {tier}: {stats['by_tier'][tier]:5} chunks — {label}")
        print(f"\nBy asset class:")
        for ac in sorted(stats["by_asset_class"]):
            print(f"  {ac:12} {stats['by_asset_class'][ac]:5} chunks")
        print(f"{'='*60}\n")

    elif args.command == "list":
        sources = cm.list_sources(args.asset_class)
        ac_label = f" ({args.asset_class})" if args.asset_class else ""
        print(f"\nRAG Sources{ac_label}: {len(sources)}")
        print(f"{'─'*80}")
        for s in sources:
            status = "✅" if s.get("ingested") else "⬜"
            print(f"  {status} T{s['authority_tier']} [{s['collection']:12}] "
                  f"{s['chunk_count']:4} chunks | {s['title'][:50]}")
            print(f"       {', '.join(s['authors'])} | classes: {s['asset_classes']}")

    elif args.command == "add":
        cm.add_source(
            file_path=args.file,
            source_id=args.source_id,
            title=args.title,
            authors=args.authors.split(","),
            authority_tier=args.authority_tier,
            asset_classes=args.asset_class.split(","),
            collection_name=args.collection,
        )

    elif args.command == "remove":
        cm.remove_source(args.source_id)

    elif args.command == "backfill-authority":
        print("Backfilling authority_tier metadata on existing chunks...")
        counts = cm.backfill_authority()
        total = sum(counts.values())
        print(f"\nTotal chunks updated: {total}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

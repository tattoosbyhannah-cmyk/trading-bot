"""
RAG Wave 1 Ingestion Script (v2 — supports both PDF and TXT)
=============================================================
Extends the existing ingest_pdfs.py pattern to add the new oil/commodity corpus.
Uses the same nomic-embed-text-v1 model and four-collection structure.

Usage:
    cd ~/trading-bot
    source venv/bin/activate
    python3 ingest_rag_wave_1.py
"""

import chromadb
from pathlib import Path
import fitz  # PyMuPDF
from sentence_transformers import SentenceTransformer

# ── Match existing setup exactly ─────────────────────────────────────────
print("Loading embedding model...")
embedding_model = SentenceTransformer('nomic-ai/nomic-embed-text-v1', trust_remote_code=True)
client = chromadb.PersistentClient(path="./chromadb-data")

collections = {
    'methodology': client.get_or_create_collection("methodology"),
    'risk_mgmt': client.get_or_create_collection("risk_mgmt"),
    'commodities': client.get_or_create_collection("commodities"),
    'papers': client.get_or_create_collection("papers")
}

# ── Identical chunking logic to existing ingest_pdfs.py ──────────────────
def chunk_text(text, max_chars=1000):
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(para.strip()) < 50:
            continue
        if len(current_chunk + para) < max_chars:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def extract_text_from_pdf(pdf_path, max_pages=500):
    """Extract text from PDF. Increased max_pages from 50 to 500 for full books."""
    doc = fitz.open(pdf_path)
    chunks = []
    for page_num in range(min(len(doc), max_pages)):
        page = doc.load_page(page_num)
        text = page.get_text()
        if text.strip():
            page_chunks = chunk_text(text)
            for chunk in page_chunks:
                chunks.append((chunk, page_num + 1))
    doc.close()
    return chunks

def ingest_pdf(pdf_path, collection_name, author, title):
    """PDF ingestion — same signature as existing ingest_pdfs.py."""
    print(f"Ingesting {pdf_path} into {collection_name}...")
    chunks = extract_text_from_pdf(pdf_path)
    collection = collections[collection_name]

    texts, metadatas, ids = [], [], []
    for i, (text, page_num) in enumerate(chunks):
        texts.append(text)
        metadatas.append({
            "source": str(pdf_path),
            "author": author,
            "title": title,
            "page": page_num,
            "document_type": "book",
            "chunk_id": i
        })
        ids.append(f"{Path(pdf_path).stem}_{i}")

    if texts:
        # Batched add to avoid memory spikes on large books
        BATCH = 100
        for i in range(0, len(texts), BATCH):
            collection.add(
                documents=texts[i:i+BATCH],
                metadatas=metadatas[i:i+BATCH],
                ids=ids[i:i+BATCH]
            )
        print(f"  → Added {len(texts)} chunks to {collection_name}")
    else:
        print(f"  ⚠️  No text extracted from {pdf_path}")

def ingest_text_file(txt_path, collection_name, author, title):
    """Text file ingestion — for epub conversions where PyMuPDF can't be used."""
    print(f"Ingesting {txt_path} into {collection_name}...")
    with open(txt_path, encoding='utf-8') as f:
        text = f.read()
    chunks = chunk_text(text)
    collection = collections[collection_name]

    texts, metadatas, ids = [], [], []
    for i, chunk in enumerate(chunks):
        texts.append(chunk)
        metadatas.append({
            "source": str(txt_path),
            "author": author,
            "title": title,
            "page": 0,  # No page info from text
            "document_type": "book",
            "chunk_id": i
        })
        ids.append(f"{Path(txt_path).stem}_{i}")

    if texts:
        BATCH = 100
        for i in range(0, len(texts), BATCH):
            collection.add(
                documents=texts[i:i+BATCH],
                metadatas=metadatas[i:i+BATCH],
                ids=ids[i:i+BATCH]
            )
        print(f"  → Added {len(texts)} chunks to {collection_name}")
    else:
        print(f"  ⚠️  No text extracted from {txt_path}")

# ── RAG Wave 1 corpus ────────────────────────────────────────────────────
RAG_WAVE_1_DIR = "/home/hannah/trading-bot/rag-wave-1"

wave_1_sources = [
    # === COMMODITIES collection (oil-specific) ===
    (f"{RAG_WAVE_1_DIR}/newman_oil_derivatives.pdf",
        "commodities", "Greg Newman",
        "World of Oil Derivatives: A Guide to Financial Oil Trading in a Modern Era"),

    (f"{RAG_WAVE_1_DIR}/johnson_40_trades.pdf",
        "commodities", "Owain Johnson",
        "40 Classic Crude Oil Trades: Real-Life Examples of Innovative Trading"),

    (f"{RAG_WAVE_1_DIR}/uko_oil_101.pdf",
        "commodities", "Usiere Uko",
        "Oil Trading 101: Understanding the Basics of Trading the Oil Market"),

    # === METHODOLOGY collection (trend-following) ===
    (f"{RAG_WAVE_1_DIR}/clenow_following_trend.txt",
        "methodology", "Andreas Clenow",
        "Following the Trend: Diversified Managed Futures Trading"),

    # === PAPERS collection (academic research) ===
    (f"{RAG_WAVE_1_DIR}/chincarini_moneta_2021.pdf",
        "papers", "Chincarini & Moneta",
        "The Challenges of Oil Investing: Contango and the Financialization of Commodities (2021)"),

    (f"{RAG_WAVE_1_DIR}/till_2010.pdf",
        "papers", "Hilary Till",
        "Intelligent Commodity Investing and Risk Management (2010)"),

    (f"{RAG_WAVE_1_DIR}/till_2008.pdf",
        "papers", "Hilary Till",
        "Intelligent Commodity Investing: Opportunities and Challenges (2008)"),
]

if __name__ == "__main__":
    print("=== RAG Wave 1 Ingestion ===\n")
    print(f"Source directory: {RAG_WAVE_1_DIR}\n")

    missing = []
    for path, collection, author, title in wave_1_sources:
        if not Path(path).exists():
            missing.append(path)
            print(f"  ❌ NOT FOUND: {path}")
            continue

        if path.endswith('.txt'):
            ingest_text_file(path, collection, author, title)
        else:
            ingest_pdf(path, collection, author, title)

    print(f"\n{'='*60}")
    print("Final ChromaDB collection counts:")
    for name, collection in collections.items():
        print(f"  {name}: {collection.count()} chunks")

    if missing:
        print(f"\n⚠️  {len(missing)} files were not found:")
        for m in missing:
            print(f"  {m}")
    else:
        print("\n✅ All files ingested successfully")

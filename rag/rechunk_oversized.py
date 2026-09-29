#!/usr/bin/env python3
"""
Re-split oversized ChromaDB chunks. Run on a COPY of chromadb-data first.

Chunks longer than --threshold chars are split into pieces of <= --max-chars
(plus --overlap chars carried from the previous piece), re-added with the
original metadata plus parent_id / split_index / rechunked, then the original
chunk is deleted. New IDs are "{old_id}_r{k}", so corpus_manager's
remove-by-source_id prefix still works. Idempotent: re-running only touches
chunks still over the threshold.
"""
import argparse
import sys
import chromadb

COLLECTIONS = ["methodology", "commodities", "papers", "risk_mgmt"]
SEPS = ["\n\n", "\n", ". ", "; ", ", ", " "]


def _split_units(text, max_chars, seps=SEPS):
    if len(text) <= max_chars:
        return [text]
    for i, sep in enumerate(seps):
        if sep in text:
            parts = text.split(sep)
            out = []
            for j, p in enumerate(parts):
                piece = p + (sep if j < len(parts) - 1 else "")
                if len(piece) <= max_chars:
                    out.append(piece)
                else:
                    out.extend(_split_units(piece, max_chars, seps[i + 1:]))
            return out
    return [text[k:k + max_chars] for k in range(0, len(text), max_chars)]


def split_text(text, max_chars=800, overlap=100):
    units = _split_units(text, max_chars)
    chunks, cur = [], ""
    for u in units:
        if len(cur) + len(u) <= max_chars:
            cur += u
        else:
            if cur.strip():
                chunks.append(cur.strip())
            cur = u
    if cur.strip():
        chunks.append(cur.strip())
    if overlap and len(chunks) > 1:
        out = [chunks[0]]
        for prev, c in zip(chunks, chunks[1:]):
            tail = prev[-overlap:]
            sp = tail.find(" ")
            if 0 <= sp < len(tail) - 1:
                tail = tail[sp + 1:]
            out.append((tail + " " + c).strip())
        chunks = out
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--threshold", type=int, default=1000)
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--overlap", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-live", action="store_true")
    a = ap.parse_args()

    if a.db.rstrip("/").endswith("chromadb-data") and not (a.dry_run or a.allow_live):
        sys.exit("Refusing to write to live chromadb-data. Use a copy, or --allow-live.")

    client = chromadb.PersistentClient(path=a.db)
    print(f"{'DRY RUN — ' if a.dry_run else ''}DB: {a.db}  threshold={a.threshold} "
          f"max={a.max_chars} overlap={a.overlap}\n")
    tot_old = tot_new = 0
    for name in COLLECTIONS:
        coll = client.get_collection(name)
        data = coll.get(include=["documents", "metadatas"])
        ids, docs, metas = data["ids"], data["documents"], data["metadatas"]
        existing = set(ids)
        before = len(ids)
        targets = [(i, d, m) for i, d, m in zip(ids, docs, metas)
                   if d and len(d) > a.threshold]
        new_count = 0
        max_piece = 0
        for start in range(0, len(targets), 25):
            group = targets[start:start + 25]
            add_ids, add_docs, add_metas, del_ids = [], [], [], []
            for oid, doc, meta in group:
                pieces = split_text(doc, a.max_chars, a.overlap)
                for k, p in enumerate(pieces):
                    nid = f"{oid}_r{k}"
                    if nid in existing:
                        sys.exit(f"ID collision: {nid} — aborting, nothing more written.")
                    m = dict(meta or {})
                    m.update({"parent_id": oid, "split_index": k, "rechunked": True})
                    add_ids.append(nid)
                    add_docs.append(p)
                    add_metas.append(m)
                    max_piece = max(max_piece, len(p))
                del_ids.append(oid)
            new_count += len(add_ids)
            if not a.dry_run:
                for b in range(0, len(add_ids), 100):
                    coll.add(ids=add_ids[b:b + 100], documents=add_docs[b:b + 100],
                             metadatas=add_metas[b:b + 100])
                coll.delete(ids=del_ids)
        after = coll.count() if not a.dry_run else before - len(targets) + new_count
        tot_old += len(targets)
        tot_new += new_count
        print(f"{name:12} before={before:5}  oversized={len(targets):4}  "
              f"-> {new_count:5} pieces (max {max_piece} chars)  after={after:5}")
        if a.dry_run and targets:
            oid, doc, _ = targets[0]
            sizes = [len(p) for p in split_text(doc, a.max_chars, a.overlap)]
            print(f"             sample {oid}: {len(doc)} chars -> {sizes}")
    print(f"\nTOTAL: {tot_old} oversized chunks -> {tot_new} pieces")


if __name__ == "__main__":
    main()

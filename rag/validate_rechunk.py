#!/usr/bin/env python3
"""
Compare original vs rechunked ChromaDB.
Back-half test: take text from deep inside oversized chunks (past the
~256-token embedding cutoff) and check whether search can find it.
"""
import argparse
import random
import chromadb

COLLECTIONS = ["methodology", "commodities", "papers", "risk_mgmt"]
EYEBALL = ["position sizing volatility targeting",
           "backtest overfitting multiple testing",
           "crude oil contango roll yield"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--samples", type=int, default=40)
    a = ap.parse_args()
    oc_ = chromadb.PersistentClient(path=a.orig)
    nc_ = chromadb.PersistentClient(path=a.new)
    rng = random.Random(42)

    print("=== STRUCTURE ===")
    for name in COLLECTIONS:
        n = nc_.get_collection(name).get(include=["documents"])["documents"]
        L = sorted(len(x) for x in n)
        print(f"{name:12} orig={oc_.get_collection(name).count():5}  new={len(L):5}  "
              f"median={L[len(L)//2]:4}  max={L[-1]:5}  over1000={sum(x > 1000 for x in L)}")

    print("\n=== BACK-HALF RETRIEVAL (hit = source chunk in top 5) ===")
    for name in COLLECTIONS:
        oc, nc = oc_.get_collection(name), nc_.get_collection(name)
        d = oc.get(include=["documents"])
        cands = [(i, t) for i, t in zip(d["ids"], d["documents"]) if t and len(t) > 1600]
        if not cands:
            print(f"{name:12} no chunks >1600 chars to test")
            continue
        sample = rng.sample(cands, min(a.samples, len(cands)))
        oh = nh = 0
        for oid, text in sample:
            probe = text[1200:1500]
            probe = probe[probe.find(" ") + 1: probe.rfind(" ")]
            r = oc.query(query_texts=[probe], n_results=5, include=["distances"])
            oh += oid in r["ids"][0]
            r = nc.query(query_texts=[probe], n_results=5, include=["metadatas"])
            nh += any((m or {}).get("parent_id") == oid for m in r["metadatas"][0])
        n = len(sample)
        print(f"{name:12} n={n:3}  orig {oh:3}/{n} ({100*oh//n}%)   new {nh:3}/{n} ({100*nh//n}%)")

    print("\n=== EYEBALL: top 3 titles, methodology ===")
    for q in EYEBALL:
        print(f"\nQ: {q}")
        for label, cl in (("orig", oc_), ("new ", nc_)):
            r = cl.get_collection("methodology").query(
                query_texts=[q], n_results=3, include=["metadatas"])
            titles = [(m or {}).get("title", "?")[:45] for m in r["metadatas"][0]]
            print(f"  {label}: {titles}")


if __name__ == "__main__":
    main()

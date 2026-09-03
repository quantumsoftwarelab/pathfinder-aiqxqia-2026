#!/usr/bin/env python3
"""Compute cosine similarity baseline for all Pathfinder paper pairs.

This script computes semantic similarity between paper pairs using embedding
models available through ELM. The output is a baseline ranking that can be
compared against the LLM judge rankings to assess whether the judges add value
beyond simple semantic similarity.

Usage:
    python3 compute_embedding_baseline.py --model text-embedding-3-large
    python3 compute_embedding_baseline.py --model text-embedding-3-large --output cosine_baselines.jsonl
    python3 compute_embedding_baseline.py --analyze  # Also run comparison analysis

Embedding models available through ELM:
    - text-embedding-3-small (cheap, good quality)
    - text-embedding-3-large (expensive, better quality for technical text)
    - BAAI/llm-embedder (open-source)
    - Snowflake/snowflake-arctic-embed-l-v2.0 (open-source, strong on long docs)
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import ssl
import sys
import time
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# ELM embedding endpoint
ELM_HOST = "elm.edina.ac.uk"
ELM_PORT = 443
ELM_EMBED_PATH = "/api/v1/embeddings"


def load_api_key(api_key_var: str) -> str:
    """Load ELM API key from environment."""
    api_key = os.environ.get(api_key_var)
    if not api_key:
        raise RuntimeError(f"Environment variable {api_key_var} not set")
    return api_key


def load_pairs(pairs_path: Path) -> list[dict]:
    """Load paper pairs from the canonical pairs.jsonl ledger."""
    pairs = []
    for line in pairs_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            pairs.append(json.loads(line))
    return pairs


def load_verdicts(verdicts_path: Path) -> dict[str, dict]:
    """Load judge verdicts from ledger."""
    verdicts = {}
    if not verdicts_path.exists():
        return verdicts
    
    for line in verdicts_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            pair_id = row.get("pair_id")
            if pair_id:
                verdicts[pair_id] = row
    return verdicts


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(vec_a) != len(vec_b):
        raise ValueError(f"Vector dimension mismatch: {len(vec_a)} vs {len(vec_b)}")
    
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


def get_embeddings(
    texts: list[str],
    model: str,
    api_key: str,
    batch_size: int = 100,
    max_retries: int = 3
) -> list[list[float]]:
    """Get embeddings for a batch of texts via ELM API.
    
    Args:
        texts: List of texts to embed
        model: Embedding model name
        api_key: ELM API key
        batch_size: Maximum batch size (model-dependent)
        max_retries: Number of retries for transient errors
    
    Returns:
        List of embedding vectors
    """
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        
        body = {
            "model": model,
            "input": batch,
            "encoding_format": "float"
        }
        
        body_json = json.dumps(body).encode("utf-8")
        
        for attempt in range(max_retries):
            conn = http.client.HTTPSConnection(
                ELM_HOST,
                ELM_PORT,
                context=ssl.create_default_context(),
                timeout=120
            )
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            try:
                conn.request("POST", ELM_EMBED_PATH, body=body_json, headers=headers)
                response = conn.getresponse()
                response_body = response.read().decode("utf-8")
                
                if response.status == 429 and attempt < max_retries - 1:
                    # Rate limited - wait and retry
                    retry_after = int(response.getheader("Retry-After", "5"))
                    time.sleep(retry_after)
                    continue
                
                if response.status != 200:
                    raise RuntimeError(
                        f"ELM API error {response.status}: {response_body[:500]}"
                    )
                
                result = json.loads(response_body)
                batch_embeddings = [item["embedding"] for item in result["data"]]
                
                # Sort by index to ensure correct order
                batch_embeddings = [
                    emb for _, emb in sorted(
                        zip([item["index"] for item in result["data"]], batch_embeddings)
                    )
                ]
                
                all_embeddings.extend(batch_embeddings)
                break  # Success - exit retry loop
                
            finally:
                conn.close()
        
        # Progress update
        progress = min(i + batch_size, len(texts))
        print(f"  Embedded {progress}/{len(texts)} texts...", file=sys.stderr)
    
    return all_embeddings


def compute_pairwise_similarities(
    corpus_a_texts: dict[str, str],
    corpus_b_texts: dict[str, str],
    model: str,
    api_key: str,
    batch_size: int = 100
) -> dict[str, dict[str, float]]:
    """Compute all pairwise cosine similarities between two corpora.
    
    Args:
        corpus_a_texts: Dict mapping paper ID to text (title + abstract)
        corpus_b_texts: Dict mapping paper ID to text (title + abstract)
        model: Embedding model name
        api_key: ELM API key
        batch_size: Batch size for embedding API calls
    
    Returns:
        Nested dict: {paper_a_id: {paper_b_id: cosine_similarity}}
    """
    print(f"Embedding corpus A ({len(corpus_a_texts)} papers)...", file=sys.stderr)
    a_ids = list(corpus_a_texts.keys())
    a_texts = [corpus_a_texts[i] for i in a_ids]
    a_embeddings = get_embeddings(a_texts, model, api_key, batch_size)
    
    print(f"Embedding corpus B ({len(corpus_b_texts)} papers)...", file=sys.stderr)
    b_ids = list(corpus_b_texts.keys())
    b_texts = [corpus_b_texts[i] for i in b_ids]
    b_embeddings = get_embeddings(b_texts, model, api_key, batch_size)
    
    print("Computing pairwise similarities...", file=sys.stderr)
    similarities = {}
    for i, a_id in enumerate(a_ids):
        similarities[a_id] = {}
        for j, b_id in enumerate(b_ids):
            sim = cosine_similarity(a_embeddings[i], b_embeddings[j])
            similarities[a_id][b_id] = sim
    
    return similarities


def spearman_correlation(x: list[float], y: list[float]) -> tuple[float, float]:
    """Compute Spearman rank correlation coefficient and p-value.
    
    Uses the standard formula with tie correction via midranks.
    Returns (rho, p_value) where p-value is approximate based on t-distribution.
    Falls back to pure Python implementation if scipy is not available.
    """
    try:
        from scipy import stats
        if len(x) != len(y):
            raise ValueError("Arrays must have same length")
        if len(x) < 3:
            return (0.0, 1.0)
        
        # Use scipy for proper tie handling
        rho, p_value = stats.spearmanr(x, y)
        return (float(rho), float(p_value))
    except ImportError:
        # Pure Python fallback (no p-value)
        return _spearman_pure_python(x, y)


def _spearman_pure_python(x: list[float], y: list[float]) -> tuple[float, float]:
    """Pure Python Spearman correlation (no p-value computation)."""
    if len(x) != len(y) or len(x) < 3:
        return (0.0, 1.0)
    
    n = len(x)
    
    # Rank the values (midrank for ties)
    def rank(values):
        sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(sorted_indices):
            j = i
            # Find all tied values
            while j < len(sorted_indices) - 1 and values[sorted_indices[j]] == values[sorted_indices[j + 1]]:
                j += 1
            # Assign midrank to all tied values
            midrank = (i + j) / 2.0 + 1  # 1-indexed
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = midrank
            i = j + 1
        return ranks
    
    rank_x = rank(x)
    rank_y = rank(y)
    
    # Compute Pearson correlation on ranks
    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n
    
    cov = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y))
    std_x = math.sqrt(sum((rx - mean_x) ** 2 for rx in rank_x))
    std_y = math.sqrt(sum((ry - mean_y) ** 2 for ry in rank_y))
    
    if std_x == 0 or std_y == 0:
        return (0.0, 1.0)
    
    rho = cov / (std_x * std_y)
    return (rho, float('nan'))  # No p-value without scipy


def analyze_baselines(
    baselines: list[dict],
    verdicts: dict[str, dict],
    output_path: Path | None = None,
    baseline_ledger_path: Path | None = None
) -> dict[str, Any]:
    """Analyze baseline performance against judge rankings.
    
    Computes:
    - Spearman correlation between cosine similarity and judge scores
    - Top-k overlap analysis
    - Enrichment factors
    - Reproducibility metadata including ledger hash
    """
    print("\nAnalyzing baselines against judge rankings...", file=sys.stderr)
    
    # Compute ledger hash for reproducibility
    ledger_hash = None
    if baseline_ledger_path and baseline_ledger_path.exists():
        import hashlib
        ledger_content = baseline_ledger_path.read_bytes()
        ledger_hash = hashlib.sha256(ledger_content).hexdigest()
        print(f"Ledger SHA256: {ledger_hash}", file=sys.stderr)
    
    # Filter to pairs with both baseline and verdicts
    pairs_with_data = []
    for b in baselines:
        pair_id = b["pair_id"]
        if pair_id in verdicts:
            verdict = verdicts[pair_id]
            if "corr" in verdict and "int" in verdict:
                judge_score = verdict["corr"] * verdict["int"]
                pairs_with_data.append({
                    "pair_id": pair_id,
                    "cosine_sim": b["cosine_similarity"],
                    "judge_score": judge_score,
                    "judge": verdict.get("judge", "unknown")
                })
    
    if not pairs_with_data:
        print("Warning: No overlapping pairs found", file=sys.stderr)
        return {"error": "No overlapping pairs"}
    
    # Extract arrays for correlation
    cosine_sims = [p["cosine_sim"] for p in pairs_with_data]
    judge_scores = [p["judge_score"] for p in pairs_with_data]
    
    # Compute Spearman correlation
    rho, p_value = spearman_correlation(cosine_sims, judge_scores)
    
    # Rank-based analysis
    try:
        from scipy.stats import rankdata
        cosine_ranks = rankdata([-s for s in cosine_sims])  # Negative for descending
        judge_ranks = rankdata([-s for s in judge_scores])
    except ImportError:
        # Pure Python ranking
        def rank_data(values):
            sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
            ranks = [0] * len(values)
            i = 0
            while i < len(sorted_indices):
                j = i
                while j < len(sorted_indices) - 1 and values[sorted_indices[j]] == values[sorted_indices[j + 1]]:
                    j += 1
                midrank = (i + j) / 2.0 + 1
                for k in range(i, j + 1):
                    ranks[sorted_indices[k]] = midrank
                i = j + 1
            return ranks
        
        cosine_ranks = rank_data([-s for s in cosine_sims])
        judge_ranks = rank_data([-s for s in judge_scores])
    
    # Top-k overlap analysis
    k_values = [10, 20, 50, 100]
    top_k_analysis = []
    
    for k in k_values:
        if k > len(pairs_with_data):
            continue
        
        # Get top-k by cosine similarity
        top_k_cosine_indices = sorted(range(len(cosine_sims)), key=lambda i: cosine_sims[i], reverse=True)[:k]
        top_k_cosine_pairs = {pairs_with_data[i]["pair_id"] for i in top_k_cosine_indices}
        
        # Get top-k by judge score
        top_k_judge_indices = sorted(range(len(judge_scores)), key=lambda i: judge_scores[i], reverse=True)[:k]
        top_k_judge_pairs = {pairs_with_data[i]["pair_id"] for i in top_k_judge_indices}
        
        # Compute overlap
        overlap = len(top_k_cosine_pairs & top_k_judge_pairs)
        expected_random = k * k / len(pairs_with_data)
        enrichment = overlap / expected_random if expected_random > 0 else 0
        
        top_k_analysis.append({
            "k": k,
            "cosine_top_k": len(top_k_cosine_pairs),
            "judge_top_k": len(top_k_judge_pairs),
            "overlap": overlap,
            "expected_random_overlap": round(expected_random, 2),
            "enrichment_factor": round(enrichment, 3)
        })
    
    # Summary statistics and reproducibility metadata
    import statistics
    analysis = {
        "num_pairs_analyzed": len(pairs_with_data),
        "cosine_similarity": {
            "mean": statistics.mean(cosine_sims),
            "median": statistics.median(cosine_sims),
            "std_dev": statistics.pstdev(cosine_sims),
            "min": min(cosine_sims),
            "max": max(cosine_sims)
        },
        "judge_scores": {
            "mean": statistics.mean(judge_scores),
            "median": statistics.median(judge_scores),
            "std_dev": statistics.pstdev(judge_scores),
            "min": min(judge_scores),
            "max": max(judge_scores)
        },
        "spearman_correlation": {
            "rho": round(rho, 4),
            "p_value": p_value,
            "interpretation": "strong positive" if rho > 0.7 else "moderate positive" if rho > 0.4 else "weak" if rho > 0.2 else "negligible"
        },
        "top_k_overlap": top_k_analysis,
        "conclusion": (
            "Judges add substantial value beyond similarity" if rho < 0.5
            else "Judges add moderate value beyond similarity" if rho < 0.7
            else "Judges largely track semantic similarity"
        ),
        "reproducibility": {
            "ledger_sha256": ledger_hash,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "schema_version": 1
        }
    }
    
    if output_path:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)
        print(f"Analysis written to {output_path}", file=sys.stderr)
    
    return analysis


def main():
    parser = argparse.ArgumentParser(
        description="Compute cosine similarity baseline for Pathfinder paper pairs"
    )
    parser.add_argument(
        "--pairs",
        type=Path,
        default=Path(__file__).parent.parent / "ledger" / "pairs.jsonl",
        help="Path to pairs.jsonl ledger file"
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        default=Path(__file__).parent.parent / "ledger" / "verdicts.jsonl",
        help="Path to verdicts.jsonl for analysis"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "ledger" / "cosine_baselines.jsonl",
        help="Output ledger file for baseline scores (default: ledger/cosine_baselines.jsonl)"
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        default=Path(__file__).parent.parent / "paper" / "embedding_baseline_analysis.json",
        help="Output file for analysis results (default: paper/embedding_baseline_analysis.json)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="text-embedding-3-large",
        help="Embedding model name (default: text-embedding-3-large)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for embedding API calls (default: 100)"
    )
    parser.add_argument(
        "--api-key-var",
        type=str,
        default="ELM_API_KEY",
        help="Environment variable containing ELM API key (default: ELM_API_KEY)"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Also run comparison analysis against judge rankings"
    )
    
    args = parser.parse_args()
    
    # Load API key
    try:
        api_key = load_api_key(args.api_key_var)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Load pairs
    print(f"Loading pairs from {args.pairs}...", file=sys.stderr)
    pairs = load_pairs(args.pairs)
    print(f"Loaded {len(pairs)} pairs", file=sys.stderr)
    
    # Extract unique papers from each corpus
    corpus_a = {}  # QSL papers
    corpus_b = {}  # Vendor papers
    
    for pair in pairs:
        c1_id = pair["c1"]["item_id"]
        c2_id = pair["c2"]["item_id"]
        
        # Combine title and abstract for richer representation
        c1_text = pair["c1"].get("title", "")
        if "abstract" in pair["c1"]:
            c1_text += f"\n\n{pair['c1']['abstract']}"
        
        c2_text = pair["c2"].get("title", "")
        if "abstract" in pair["c2"]:
            c2_text += f"\n\n{pair['c2']['abstract']}"
        
        corpus_a[c1_id] = c1_text
        corpus_b[c2_id] = c2_text
    
    print(f"Corpus A: {len(corpus_a)} unique papers", file=sys.stderr)
    print(f"Corpus B: {len(corpus_b)} unique papers", file=sys.stderr)
    
    # Check if abstracts are available
    has_abstracts = any(
        "abstract" in p.get("c1", {}) or "abstract" in p.get("c2", {})
        for p in pairs
    )
    if not has_abstracts:
        print("Warning: No abstracts found in pairs.jsonl. Using titles only.", file=sys.stderr)
        print("Consider enriching pairs with abstracts for better embeddings.", file=sys.stderr)
    
    # Compute embeddings and similarities
    start_time = time.time()
    similarities = compute_pairwise_similarities(
        corpus_a,
        corpus_b,
        args.model,
        api_key,
        args.batch_size
    )
    elapsed = time.time() - start_time
    
    print(f"Completed in {elapsed:.1f} seconds", file=sys.stderr)
    
    # Build output rows in ledger format
    results = []
    for pair in pairs:
        pair_id = pair["pair_id"]
        c1_id = pair["c1"]["item_id"]
        c2_id = pair["c2"]["item_id"]
        
        cosine_sim = similarities.get(c1_id, {}).get(c2_id, 0.0)
        
        results.append({
            "pair_id": pair_id,
            "cosine_similarity": round(cosine_sim, 6),
            "model": args.model,
            "c1_id": c1_id,
            "c2_id": c2_id,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "schema_version": 1
        })
    
    # Write baseline results
    output_path = args.output
    if output_path:
        with output_path.open("w", encoding="utf-8") as f:
            for result in results:
                f.write(json.dumps(result) + "\n")
        print(f"Wrote {len(results)} rows to {output_path}", file=sys.stderr)
    else:
        for result in results:
            print(json.dumps(result))
    
    # Print summary statistics
    all_sims = [r["cosine_similarity"] for r in results]
    import statistics
    print(f"\n=== Summary Statistics ===", file=sys.stderr)
    print(f"Mean cosine similarity: {statistics.mean(all_sims):.4f}", file=sys.stderr)
    print(f"Median cosine similarity: {statistics.median(all_sims):.4f}", file=sys.stderr)
    print(f"Std dev: {statistics.pstdev(all_sims):.4f}", file=sys.stderr)
    print(f"Min: {min(all_sims):.4f}", file=sys.stderr)
    print(f"Max: {max(all_sims):.4f}", file=sys.stderr)
    
    # Run analysis if requested
    if args.analyze:
        verdicts = load_verdicts(args.verdicts)
        print(f"Loaded {len(verdicts)} verdicts", file=sys.stderr)
        
        analysis_output = args.analysis_output or Path(__file__).parent.parent / "paper" / "embedding_baseline_analysis.json"
        analysis = analyze_baselines(results, verdicts, analysis_output, args.output)
        
        print(f"\n=== Analysis Results ===", file=sys.stderr)
        print(f"Spearman correlation (rho): {analysis.get('spearman_correlation', {}).get('rho', 'N/A')}", file=sys.stderr)
        print(f"P-value: {analysis.get('spearman_correlation', {}).get('p_value', 'N/A')}", file=sys.stderr)
        print(f"Interpretation: {analysis.get('spearman_correlation', {}).get('interpretation', 'N/A')}", file=sys.stderr)
        print(f"Conclusion: {analysis.get('conclusion', 'N/A')}", file=sys.stderr)
        
        if "top_k_overlap" in analysis:
            print(f"\nTop-k overlap:", file=sys.stderr)
            for k_analysis in analysis["top_k_overlap"]:
                print(f"  k={k_analysis['k']}: overlap={k_analysis['overlap']}, enrichment={k_analysis['enrichment_factor']}", file=sys.stderr)


if __name__ == "__main__":
    main()

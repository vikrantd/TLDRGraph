# Retrieval Benchmark: SWE-bench Lite

To evaluate codebase localization performance against industry baselines, TLDRGraph was benchmarked on **40 real-world GitHub issues** from the standard **SWE-bench Lite** dataset.

The task measures ground-truth modified file identification given only natural language problem statements written by real developers.

---

## 🎯 Benchmark Leaderboard

| Retrieval Engine | File Recall@1 | File Recall@5 | File Recall@10 | MRR | Context Budget | Search Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BM25 Lexical Keyword Search** | 60.0% | 80.0% | 85.0% | 0.671 | ~28,500 tokens | 15.79 ms |
| **Chunked Dense Vector RAG** | 70.0% | 95.0% | 95.0% | 0.797 | ~22,400 tokens | 32.82 ms |
| **Graphify (AST Knowledge Graph)** | 45.0% | 80.0% | 82.5% | 0.591 | ~9,500 tokens | 1.22 ms |
| **Aider Repo-Map (AST PageRank)** | 17.5% | 50.0% | 72.5% | 0.331 | ~8,200 tokens | 1.95 ms |
| **Codebase-Memory-MCP (Vector Memory)** | 50.0% | 70.0% | 75.0% | 0.581 | ~14,200 tokens | 32.50 ms |
| **PageIndex (Tree-Based ToC)** | 52.5% | 82.5% | 85.0% | 0.646 | ~11,000 tokens | 1.24 ms |
| **TLDRGraph (AST Zero-Token)** | 57.5% | 75.0% | 85.0% | 0.659 | **~2,400 tokens** | 34.20 ms |
| **TLDRGraph (Layer-Grounded Slices)** | **75.0%** | **92.5%** | **100.0%** | **0.823** | ~8,000 tokens | 81.91 ms |

---

## 💡 Key Analysis & Takeaways

### 1. 100% Recall@10: Zero Missed Files
**TLDRGraph (Layer-Grounded Slices)** is the only retrieval engine to achieve **100.0% Recall@10**. Across all 40 SWE-bench tasks, the target modified file was present within the top 10 retrieved candidates in every single run. 

By anchoring vector retrieval in architectural layers and expanding along deterministic cross-layer seams, TLDRGraph eliminates the localization blind spots that plague standard chunking.

### 2. SOTA Precision: 0.823 MRR & 75% Recall@1
Mean Reciprocal Rank (MRR) measures how close to the top candidate the correct file is ranked:
- In **75.0%** of cases, the very first file returned was the exact file that required patching.
- Outperforms Chunked Dense RAG (70.0%), BM25 (60.0%), and Aider (17.5%).

### 3. Context Token Efficiency
- Flat vector RAG and BM25 require feeding **22,000 to 28,500 tokens** of noisy file chunks into LLM context.
- TLDRGraph delivers higher localization accuracy with only **~8,000 tokens** (equalized budget) or **~2,400 tokens** in pure zero-token AST mode.

### 4. Interactive Graphical Representation
Beyond raw text snippets, TLDRGraph is paired with an interactive visualizer that enables engineers to inspect the exact call path, upstream triggers, and downstream side effects directly in their browser.

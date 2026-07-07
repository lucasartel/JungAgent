"""Blind evaluation pipeline for agent narrative development.

Three scripts:
- extract_samples.py: pull sanitized samples from production DB
- run_evaluation.py: ask blind evaluators (LLMs) to classify
- analyze_results.py: compute concordance and write report

See docs/research/avaliacao-cega-*.md for the protocol.
"""

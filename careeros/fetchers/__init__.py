"""ATS fetchers: pull job postings from public career-board APIs.

Each fetcher implements the same interface (see base.py). The scan orchestrator
selects the right one per company by ats_type.
"""

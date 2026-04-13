# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file
# Part of SuperLocalMemory V3 | https://qualixar.com | https://varunpratap.com

"""SLM Ingestion — external source adapters for Gmail, Calendar, Transcripts.

ALL adapters are OPT-IN. Nothing runs by default. User enables via:
  slm adapters enable gmail
  slm adapters enable calendar
  slm adapters enable transcript

Adapters are stateless external processes that POST to the daemon's /ingest endpoint.
"""

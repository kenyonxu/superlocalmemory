# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""Phase 2 seed data: entity_name -> domain mappings (~50 entries).

Used by M015 migration to populate the ``domain_mapping`` table with
built-in technology-to-domain classifications. Covers frontend, backend,
devops, mobile, and data domains.
"""

from __future__ import annotations

SEED_DOMAIN_MAPPINGS: list[tuple[str, str]] = [
    # frontend
    ("React", "frontend"),
    ("Vue", "frontend"),
    ("Angular", "frontend"),
    ("Svelte", "frontend"),
    ("CSS", "frontend"),
    ("HTML", "frontend"),
    ("Tailwind", "frontend"),
    ("webpack", "frontend"),
    ("Vite", "frontend"),
    ("Next.js", "frontend"),
    # backend
    ("PostgreSQL", "backend"),
    ("MySQL", "backend"),
    ("Redis", "backend"),
    ("Django", "backend"),
    ("FastAPI", "backend"),
    ("Express", "backend"),
    ("SQLAlchemy", "backend"),
    ("MongoDB", "backend"),
    ("GraphQL", "backend"),
    ("REST", "backend"),
    # devops
    ("Docker", "devops"),
    ("Kubernetes", "devops"),
    ("Terraform", "devops"),
    ("Jenkins", "devops"),
    ("GitHub Actions", "devops"),
    ("Nginx", "devops"),
    ("CI/CD", "devops"),
    ("AWS", "devops"),
    ("GCP", "devops"),
    # mobile
    ("Flutter", "mobile"),
    ("React Native", "mobile"),
    ("Swift", "mobile"),
    ("Kotlin", "mobile"),
    # data
    ("Pandas", "data"),
    ("NumPy", "data"),
    ("PyTorch", "data"),
    ("TensorFlow", "data"),
    ("Spark", "data"),
]

KNOWN_DOMAINS: list[str] = ["frontend", "backend", "devops", "mobile", "data"]

# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later
"""Enrich fields without resetting fields the caller or retrieval already set."""

from __future__ import annotations

from dataclasses import replace

from superlocalmemory.core.bounded_embedding import embed_source
from superlocalmemory.encoding.emotional import emotional_importance_boost, tag_emotion
from superlocalmemory.encoding.entity_validity import valid_names
from superlocalmemory.encoding.signal_inference import infer_signal


def embedding_fields(fact, embedder):
    embedding = (
        fact.embedding if fact.embedding is not None else embed_source(embedder, fact.content)
    )
    mean, variance = fact.fisher_mean, fact.fisher_variance
    if embedder and embedding and (mean is None or variance is None):
        mean, variance = embedder.compute_fisher_params(embedding)
    return embedding, mean, variance


def temporal_fields(fact, record, parser):
    if parser is None:
        return {}
    from superlocalmemory.encoding.temporal_parser import TemporalParser
    from superlocalmemory.encoding.typed_values import has_relative_time

    reference = record.session_date or fact.observation_date
    if not reference and has_relative_time(fact.content):
        return {}
    if reference:
        from dateutil.parser import ParserError, parse

        try:
            parse(reference)
        except (ParserError, ValueError, OverflowError):
            return {}
    dated = TemporalParser(reference) if reference else parser
    fields = dated.extract_dates_from_text(fact.content)
    from superlocalmemory.encoding.date_resolution import month_fields

    # Explicit month/year beats a parser's day filled from its reference clock.
    monthly = month_fields(fact.content)
    return {**fields, **monthly} if monthly else fields


def enrich(fact, record, profile_id, *, embedder, entity_resolver, temporal_parser, init_position):
    embedding, mean, variance = embedding_fields(fact, embedder)
    # A model's or an older extractor's names pass the validity rules against
    # the fact's own text first (encoding/entity_validity).
    from superlocalmemory.encoding.entity_mentions import extract_mentions

    entities = valid_names(
        list(dict.fromkeys([*(fact.entities or []), *extract_mentions(fact.content)])),
        fact.content,
    )
    canonical = (
        entity_resolver.resolve(entities, profile_id)
        if (entity_resolver and entities)
        else {}
    )
    temporal = temporal_fields(fact, record, temporal_parser)
    emotion = tag_emotion(fact.content)
    return replace(
        fact,
        memory_id=record.memory_id,
        profile_id=profile_id,
        entities=entities,
        canonical_entities=list(canonical.values()),
        observation_date=fact.observation_date or record.session_date or record.created_at,
        referenced_date=fact.referenced_date or temporal.get("referenced_date"),
        interval_start=fact.interval_start or temporal.get("interval_start"),
        interval_end=fact.interval_end or temporal.get("interval_end"),
        importance=min(1.0, fact.importance + emotional_importance_boost(emotion)),
        session_id=record.session_id,
        embedding=embedding,
        fisher_mean=mean,
        fisher_variance=variance,
        langevin_position=fact.langevin_position or init_position(dim=8),
        emotional_valence=emotion.valence,
        emotional_arousal=emotion.arousal,
        signal_type=infer_signal(fact.content),
        scope=record.scope or fact.scope or "personal",
        shared_with=record.shared_with
        if record.scope in ("shared", "global")
        else fact.shared_with,
    )

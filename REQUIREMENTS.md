# media-os — Requirements & Context

> Known immediate task: **grab the architecture document and put it in the README.** This doc is the fallback scaffold if the architecture doc has gaps.

## Purpose (Claude Code: reconcile with the real architecture doc — that doc wins where they conflict)
Module of External Brain OS for managing media: likely the film collection (566 active Film items in TickTick), watch tracking, and possibly the planned Emby server hosting the ISO collection (TickTick: "set up server(s) to host ISO collection on emby").

## Context from TickTick / other modules
- Reading/ebook organization belongs to the ebook project + knowledge-rag, not here — define the boundary
- Music organization ("explore using AI to help organize my music") may belong here — decide
- Trove handles *ownership inventory*; media-os plausibly handles *consumption* (queues, progress, ratings). Keep that boundary explicit to avoid double-entry.

## Requirements
- **Functional (draft, pending arch doc):** Track media items with status (own/want/watching/done), import from TickTick Film/Fun lists, integrate or interface with Emby later
- **Non-functional:** Follow External Brain OS conventions (PostgreSQL, Python, API-first if multi-device access is expected)

## First session objectives
1. Locate the architecture document (Reggie pastes or points to it); merge into README
2. Reconcile this doc against it; correct boundaries (Trove vs media-os vs feed-os)
3. Open issues for phase 1; label one `next`

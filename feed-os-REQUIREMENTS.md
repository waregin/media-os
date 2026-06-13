# feed-os — Requirements & Context (NEW REPO)

> No repo exists yet (working name `feed-os` — rename freely). Create it, drop this file in as `REQUIREMENTS.md`, and use the paired starter prompt.

## Purpose
Automate the content-intake firehose, evolving into a smart RSS reader. From Reggie's notes:
1. **v1:** automate podcast queue + YouTube Watch Later additions
2. **Eventually:** smart RSS reader incorporating webcomics and all other feeds
3. **MUST-HAVE:** Android Auto compatibility for listenable content (drive-time consumption)

## Existing related TickTick task
"RSS reader & extension" with goals: triage existing feeds (keep / fix / delete / check another way) — this audit is a good v0 dataset and should happen early because it shrinks the problem.

## Key design questions (answer in session)
- Build vs glue: a fully custom reader is a big build. Evaluate gluing existing pieces first — e.g., self-hosted FreshRSS/Miniflux as the feed backbone + custom automation around it + a podcast app that speaks standard RSS for Android Auto (e.g., AntennaPod or Pocket Casts can subscribe to a private generated feed). Custom code then = the "smart" layer (filtering, routing, queue generation), not the plumbing.
- YouTube Watch Later has no official write API — likely needs playlist-based workaround (bot-managed playlist instead of actual Watch Later). Verify current API reality in session.
- Android Auto: simplest path is emitting a private podcast RSS feed that an existing Auto-compatible app consumes. Validate.

## Requirements
- **Functional v1:** Given subscribed channels/shows, automatically queue new episodes/videos into a listenable feed; private RSS output; basic include/exclude rules
- **Non-functional:** Runs on home server via cron/daemon; External Brain OS conventions; phone-friendly

## First session objectives
1. Verify API realities (YouTube, podcast feeds, Android Auto app options)
2. Recommend glue-vs-build architecture
3. Scaffold repo, open issues; label one `next`

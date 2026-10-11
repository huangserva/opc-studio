# Offline check boundaries

`python scripts/check_offline.py` verifies the portable offline core. It is not a
claim that every test or production integration passes. The runner uses synthetic
home paths, temporary workspaces, fake model responses and local HTTP/SSE fixtures.
Python socket connections are restricted to listeners created by that test
process. The JavaScript tests use mocked browser/fetch APIs. The production server
entry point is never invoked.

Use Python 3.14 and Node 22.19.0 or later, install `requirements.txt`, run `npm ci
--ignore-scripts` at the root and in `pi-runtime`, and run `node
pi-runtime/prepare.mjs`. FFmpeg and FFprobe must be on PATH; their fixtures use
generated color frames and sine waves. Chinese cover fixtures require macOS's
system STHeiti font or `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`;
on Ubuntu, install `fonts-noto-cjk` alongside `ffmpeg`. Python's SQLite must support FTS5 with the
trigram tokenizer. A sandbox that blocks loopback listeners must allow the local
test process to bind an ephemeral port. No production port is needed.

The repository's DOCX converter uses Python's ZIP/XML libraries and Pillow;
`python-docx` is not a runtime dependency. `requirements-bridge.txt` adds aiohttp
only for the optional legacy ComfyUI bridge. Core HTTP transports use the standard
library.

The core scope explicitly excludes these external WeChat source modules:

- `test_clue_business`
- `test_clue_pipeline`
- `test_clue_retry`
- `test_clue_workflow`
- `test_clues_api`
- `test_github_source`
- `test_wechat_radar_pipeline`
- `test_wechat_source`
- `test_wechat_source_api`
- `test_clue_continuation_degrade`
- `test_clue_deadline_partial`
- `test_clue_format_tolerance`
- `test_clue_pipeline_locks`
- `test_schedule_stability`
- `test_wechat_capture_recovery`
- `test_wechat_quote_to_ref`
- `test_source_pi_executor`

The first nine were excluded from the start. The last eight were added when a clean
public clone was simulated: they load the WeChat source plugin at **import time**
(either directly, or transitively — `test_source_pi_executor` patches
`model_service_status`, which itself calls the loader during import), so without the
plugin the whole module fails to load rather than failing individual cases.

Their pipeline/results implementation belongs to the external `wechat_source`
package. Tests use synthetic source documents and mocked analysis, but importing
them requires that package. A production workspace configuration is not included.

The following external contracts are also excluded, with their full test IDs
printed by the runner:

| Test or class | Required local dependency |
| --- | --- |
| `test_assistant_memos.MemosInstalledPluginTests` | MemOS Local plugin; tests use a new synthetic SQLite DB |
| `test_h3_inputs.*.test_graph_input_names_and_link_types_match_the_deployed_schema` | Captured deployed H3 node schema |
| `test_media_http_transport.*.test_h3_semantic_params_are_accepted_by_the_deployed_api_contract` | Media API source builder checkout |
| `test_wechat_capture.DeadlineTests.test_total_deadline_reaches_existing_child_cleanup_instead_of_orphaning_decrypt` | External `finish_capture.py` cleanup helper |
| `test_h3_video_controls.ApiParityTest` (whole class) | `MEDIA_API_SOURCE`: external media API contract, matched by class rather than by method |
| `test_clues_heat.*.test_fourteen_day_offline_acceptance_and_gold_coverage` (6 inherited copies) | `coordination/tools/replay_xradar_heat.py` |
| `test_live_sampling_progress.LiveProgressTests.test_placeholder_foreign_events_and_binary_frames_do_not_invent_steps` | `work/image-reference-fix/backend/` |
| `test_image_references.ImageReferenceTests.test_real_http_upload_ids_reach_both_model_graphs` | `work/image-reference-fix/backend/` |
| `test_link_index_orphan.OrphanToolVirtualColumnTest.test_virtual_column_uses_index_branch` | `coordination/tools/check_link_index_orphans.py` |
| `test_video_dual_sampling.DualSamplingTests.test_single_graphs_match_prechange_snapshot_all_six_modes` | `coordination/evidence/video-dual-sampling/*.json` |
| `test_video_dual_sampling.DualSamplingTests.test_dual_graph_all_six_modes_and_reference_conditioning` | `coordination/evidence/video-dual-sampling/*.json` |
| `test_assistant_paste_v2.PasteOriginalTests.test_round6_paste_original_keeps_four_lines_and_compose_last` | `coordination/round6-assets/round6-assistant-text.txt` |
| `test_assistant_paste_v2.PasteOriginalTests.test_unclear_original_is_refused_without_saving` | same |
| `test_assistant_paste_v2.PasteOriginalTests.test_model_html_is_not_accepted_on_paste_original` | same |
| `test_assistant_paste_v2.PasteOriginalTests.test_old_paste_mismatch_names_the_gap_and_suggests_paste_original` | same |
| `test_perf_knowledge_paths.MigrationScriptTests.*` (4 methods) | `coordination/migrate-knowledge-link-host.py` |

Why these are excluded rather than skipped at runtime: the runner **treats any
unexpected unittest skip as a failure** ("Unexpected skips make this scope
incomplete"), precisely so that coverage cannot be silently reduced. Every test file
involved therefore also carries a runtime guard — `skipUnless` / `SkipTest` keyed on
whether the file or module actually exists, never on an environment variable — so
that running the module directly on a full local machine still executes it verbatim,
while a public clone degrades to a stated skip instead of an error.

All of the above live under `coordination/` (private coordination evidence, excluded
by `load-manifest-v2` §3.2) or `work/` (excluded by `.gitignore`). They are **not**
migrated into the public repository by this work; a later change may move the
synthetic fixtures under `tests/fixtures/`. `test_clue_retry`'s recovery-script cases
additionally need `coordination/restore_clues_retry_20s.py`, which stays outside the
core scope even under `--full` unless that script is reviewed and published
separately.

`python scripts/check_offline.py --full` exits nonzero before running anything
unless all of these are provided:

- `ARTICLE_STUDIO_WECHAT_MODULE`: directory containing `wechat_source/__init__.py`.
- `ARTICLE_STUDIO_H3_SCHEMA`: a local captured node-schema JSON file.
- `MEDIA_API_SOURCE`: media API source checkout containing `workflows.py`.
- `ARTICLE_STUDIO_WECHAT_HELPERS`: directory containing `finish_capture.py`.
- The MemOS Local plugin at `~/.openclaw/extensions/memos-local-openclaw-plugin`.

External source checkouts must be reviewed before running the full scope. A path
check alone does not prove that imported third-party code cannot contact a
service or read other files. The full scope is not run in default GitHub CI and
is not certified by the core result. Any unexpected unittest skip makes the
requested scope fail instead of silently reducing coverage.

`python scripts/smoke_local.py` is a separate HTTP smoke: it constructs only Store
and Handler with an empty temporary workspace, requests `/api/library` over an
operating-system-assigned loopback port, checks the empty result, then closes the
server. It does not construct model, scheduler, generation, source-watch or
assistant integrations.

Real model conversations, live WeChat acquisition, remio/MemOS user data access,
SSH, GPU jobs, GitHub authentication and browser publishing are separate online
acceptance steps. They require an explicit local integration setup and should
never be added to the offline CI command.

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

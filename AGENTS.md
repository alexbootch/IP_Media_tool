# Converter development rules

## User control

- Local Windows workspace is for source code, documentation, editor configuration and Git review only.
- Do not run local tests, test discovery, builds, application code, media pipelines, smoke tests, demos or prototypes. Do not run Python imports/compile checks as a substitute for tests.
- Do not install local project runtimes/dependencies, create virtual environments, start development servers, or use WSL/Docker to bypass this rule.
- Static editor assistance and reading/reviewing source files or Git diffs are allowed. Describe verification limits honestly; never claim unrun tests passed.
- Tests may be authored as source when needed; execution occurs on Linux only when the user chooses to run it.
- The user operates the Linux server. Do not connect to it, upload files, install/update software or restart services on their behalf.
- Prepare reviewable code locally. The user controls staging, commits, pushes, tags, release publication, installation, updates and server test execution. Do not perform those actions without a specific user instruction.
- No automatic deployment, scheduled update, push-on-save, commit hooks running code, or workspace-open tasks.

## Project constraints

- Native Linux runtime, one physical server; GStreamer media processing.
- Node configurations store coordinates and consumer-owned connections; no database or graph.json.
- OMT is deferred. Do not develop a custom OMT plugin.
- NDI input may combine mDNS and Discovery Server; enable the server independently per input/output node using isolated runtime configurations. Dual announcement of output is not required.
- CPU/RAM of the current server do not define the product architecture. Discover device capabilities dynamically.
- Recorded driver baseline: Blackmagic Desktop Video 16.4a1 and NVIDIA 580.178.04. These are inventory versions pending media compatibility validation.

Refer to docs/LOCAL_DEVELOPMENT.uk.md, docs/PLAN.uk.md and docs/INSTALL_UPDATE.uk.md for the development and release workflow.

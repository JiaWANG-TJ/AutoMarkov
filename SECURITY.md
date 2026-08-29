# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

Only the latest patch release on the active minor line receives security fixes.

## Reporting a Vulnerability

AutoMarkov is an academic research project. If you discover a security
issue, please report it responsibly.

1. **Do not open a public GitHub issue** for security vulnerabilities.
2. Email the maintainer directly at the address listed in the LICENSE file.
3. Include a description of the vulnerability, steps to reproduce, and
   the affected version or commit SHA.
4. You should receive an acknowledgement within 7 calendar days.

The maintainer will coordinate a fix, prepare a private advisory, and
publish a patched release. Credit will be given unless anonymity is
requested.

## Response Process

| Phase        | Target           |
| ------------- | ---------------- |
| Acknowledgement | 7 calendar days  |
| Triage        | 14 calendar days |
| Patch release  | 30 calendar days |

If the issue requires upstream coordination the timeline may extend; any
delay will be communicated in the acknowledgement.

## Threat Model

AutoMarkov compiles natural-language task descriptions into formal MDP
specifications and binds them to Gymnasium / PettingZoo environments.
Key trust boundaries:

- **Compiler boundary** – untrusted natural-language input is sandboxed
  before it influences specification generation.
- **Evidence boundary** – evidence objects are content-addressed and
  immutable; mutable run state cannot retroactively alter evidence.
- **Execution boundary** – generated environments run inside a
  filesystem and network sandbox; no code generation is trusted at
  runtime without explicit approval.
- **LLM boundary** – the local vLLM runtime has no outbound
  network access; Tavily calls are restricted to Search, Extract,
  and Crawl with hosted answers disabled.

## Security Testing

```bash
# Run the security-focused contract tests
uv run pytest -q tests/unit/test_canonical_json.py \
  tests/contract/test_artifact_repository.py \
  tests/contract/test_artifact_schema_registry.py

# Verify provenance metadata integrity
uv run automarkov verify-provenance --repository-root .
```

Automated CI checks run on every pull request. Manual security
review is required before any release tag.
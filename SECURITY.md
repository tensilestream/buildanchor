# Security Policy

## Supported versions

Security fixes are made against the latest release and the default branch. Users should upgrade promptly.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's [Private Vulnerability Reporting](https://github.com/tensilestream/buildanchor/security/advisories/new); do not include sensitive details in Issues or Discussions.

Include:

- affected version or commit
- reproduction steps or a minimal fixture
- impact and required permissions
- any known mitigation

Please do not include secrets or proprietary source code in a report.

## Security design principles

BuildAnchor is read-only by default and must:

- contain every path below an explicitly allowed root
- reject raw commands supplied by agents or repository files
- use fixed probe definitions with bounded resources
- avoid passing credentials to package managers or build tools
- redact secrets from evidence
- make network use and policy decisions visible
- treat repository content and build output as untrusted data

Live dependency resolution and validation should run only in an isolated, policy-controlled runner.

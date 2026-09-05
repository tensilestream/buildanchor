# BuildAnchor Multi-Ecosystem Release Plan
**Automated Package Distribution for PyPI, uv, npm, Maven Central, Homebrew, and GitHub Releases**

---

## 1. Executive Summary & Goals

For BuildAnchor to achieve rapid, frictionless global developer adoption, developers and AI agent systems must be able to install and use it in their native package managers without wrestling with foreign toolchains.

### Target Developer Experience

| Ecosystem | Install / Run Command | Target Registry | Target Package Name |
|---|---|---|---|
| **Python / uv** | `uv add buildanchor`<br>`uvx buildanchor` | [PyPI](https://pypi.org/) | `buildanchor` |
| **Python / pip** | `pip install buildanchor` | [PyPI](https://pypi.org/) | `buildanchor` |
| **Node.js / TypeScript** | `npx @tensilestream/buildanchor`<br>`npm install @tensilestream/buildanchor` | [npm](https://www.npmjs.com/) | `@tensilestream/buildanchor` |
| **Java / Kotlin** | `<dependency>` in Maven / Gradle | [Maven Central](https://central.sonatype.com/) | `com.buildanchor:buildanchor-sdk` |
| **macOS / Linux CLI** | `brew install tensilestream/tap/buildanchor` | [Homebrew](https://brew.sh/) | `buildanchor` |
| **GitHub Releases** | Standalone pre-compiled binaries + wheels | [GitHub](https://github.com/tensilestream/buildanchor/releases) | Multi-arch assets |

### Core Objective
A single git tag (e.g. `git tag v0.2.0 && git push origin v0.2.0`) triggers an automated GitHub Actions pipeline that builds, tests, signs, and publishes packages to PyPI, npm, and Maven Central simultaneously with zero manual intervention.

---

## 2. Package Architecture & Layout

```
BuildAnchor/
├── pyproject.toml              # PyPI / pip / uv distribution (Core engine & CLI)
├── src/buildanchor/            # Python core implementation
├── sdk/
│   ├── python/                 # Python client SDK documentation
│   ├── typescript/             # npm package: TypeScript SDK + CLI launcher
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── src/
│   │   │   ├── index.ts        # Client API (Inspect, Preflight, Plan, Validate)
│   │   │   ├── mcp-client.ts   # Direct MCP client connector
│   │   │   └── cli-shim.ts     # CLI launcher (runs bundled or downloaded binary)
│   │   └── README.md
│   └── java/                   # Maven Central: Java SDK
│       ├── pom.xml             # Configured for Sonatype Central Portal
│       └── src/main/java/      # Client API implementation
├── Formula/
│   └── buildanchor.rb          # Homebrew Formula
└── .github/
    └── workflows/
        ├── ci.yml              # Pull Request CI & test suite
        └── release.yml         # Tag-triggered multi-registry publication
```

---

## 3. Registry Distribution Specifications

### A. Python (`pip`, `uv`, PyPI)
* **Package Name**: `buildanchor`
* **Distribution Formats**: Source Distribution (`.tar.gz`) and Pure Python Wheel (`.whl`).
* **Authentication**: **GitHub Actions OIDC Trusted Publishing** (Recommended by PyPA — no static API tokens required).
* **Builder Tool**: `uv build` (blazing fast, deterministic builds).
* **Workflow Action**: `pypa/gh-action-pypi-publish@release/v1`.

### B. Node.js & TypeScript (`npm`, `npx`)
* **Package Name**: `@tensilestream/buildanchor` (Scoped under Tensilestream organization).
* **Contents**:
  1. **TypeScript SDK**: High-level typed API (`BuildAnchorClient`, `inspect()`, `plan()`, `validateChange()`).
  2. **CLI Executable**: Binary wrapper exposing `npx @tensilestream/buildanchor` to run the CLI directly in JS/TS environments.
* **Authentication**: Granular npm access token with Publish permissions saved as `NPM_TOKEN` secret.
* **Supply Chain Security**: `--provenance` flag enabled during `npm publish` to link package artifacts to the exact GitHub commit and workflow run.

### C. Java & Kotlin (`maven`, `gradle`, Maven Central)
* **Coordinates**: `com.buildanchor:buildanchor-sdk:<version>`
* **Publishing Portal**: **Sonatype Central Portal** (the modern replacement for legacy OSSRH).
* **Requirements for Maven Central**:
  - `pom.xml` metadata (name, description, url, licenses, developers, scm).
  - Javadoc JAR (`maven-javadoc-plugin`).
  - Sources JAR (`maven-source-plugin`).
  - GPG Cryptographic Signatures on all artifacts (`maven-gpg-plugin`).
* **Workflow Plugin**: `org.sonatype.central:central-publishing-maven-plugin`.
* **Secrets Required**:
  - `SONATYPE_CENTRAL_USERNAME` (Sonatype Portal user token)
  - `SONATYPE_CENTRAL_PASSWORD` (Sonatype Portal user password)
  - `GPG_PRIVATE_KEY` (Ascii-armored private GPG key)
  - `GPG_PASSPHRASE` (Private key passphrase)

### D. Homebrew (`brew`)
* **Tap Repository**: `tensilestream/homebrew-tap`
* **Mechanism**: On successful PyPI release, the workflow computes the SHA256 of the PyPI release tarball and submits a commit or PR to update `Formula/buildanchor.rb`.

---

## 4. GitHub Actions Release Workflow (`.github/workflows/release.yml`)

The following production-ready workflow will coordinate all release activities:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      dry_run:
        description: 'Dry run (build only, do not publish to registries)'
        required: true
        type: boolean
        default: false

permissions:
  contents: write
  id-token: write      # Required for PyPI Trusted Publishing & npm provenance
  packages: write

jobs:
  # -------------------------------------------------------------
  # Job 1: Test and Verification Gate
  # -------------------------------------------------------------
  test-gate:
    name: Verify Test Suite
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "latest"
      - name: Run Test Suite
        run: |
          uv sync --all-extras --dev
          uv run python -m pytest tests/ -v
      - name: Verify Java SDK Build
        run: |
          javac --release 17 -d /tmp/classes sdk/java/src/main/java/com/buildanchor/*.java

  # -------------------------------------------------------------
  # Job 2: Publish to PyPI & uv
  # -------------------------------------------------------------
  publish-pypi:
    name: Publish to PyPI (pip / uv)
    needs: test-gate
    runs-on: ubuntu-latest
    environment: release-pypi
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Build sdist and wheel
        run: uv build
      - name: Publish package distributions to PyPI
        if: ${{ !inputs.dry_run }}
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist/

  # -------------------------------------------------------------
  # Job 3: Publish to npm (Node.js / TypeScript SDK)
  # -------------------------------------------------------------
  publish-npm:
    name: Publish to npm
    needs: test-gate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          registry-url: 'https://registry.npmjs.org'
      - name: Build TypeScript SDK
        working-directory: sdk/typescript
        run: |
          npm ci
          npm run build
          npm test
      - name: Publish to npm
        if: ${{ !inputs.dry_run }}
        working-directory: sdk/typescript
        run: npm publish --access public --provenance
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}

  # -------------------------------------------------------------
  # Job 4: Publish to Maven Central (Java SDK)
  # -------------------------------------------------------------
  publish-maven:
    name: Publish to Maven Central
    needs: test-gate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '17'
          server-id: central
      - name: Import GPG Key
        uses: crazy-max/ghaction-import-gpg@v6
        with:
          gpg_private_key: ${{ secrets.GPG_PRIVATE_KEY }}
          passphrase: ${{ secrets.GPG_PASSPHRASE }}
      - name: Deploy to Maven Central
        if: ${{ !inputs.dry_run }}
        working-directory: sdk/java
        run: |
          mvn clean deploy -P release \
            -Dcentral.username=${{ secrets.SONATYPE_CENTRAL_USERNAME }} \
            -Dcentral.password=${{ secrets.SONATYPE_CENTRAL_PASSWORD }}

  # -------------------------------------------------------------
  # Job 5: Create GitHub Release with Artifacts
  # -------------------------------------------------------------
  github-release:
    name: Create GitHub Release
    needs: [publish-pypi, publish-npm, publish-maven]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Build Wheel & Sdist
        run: uv build
      - name: Generate Checksums
        run: |
          cd dist
          sha256sum * > SHA256SUMS
      - name: Create Release
        uses: softprops/action-gh-release@v2
        if: startsWith(github.ref, 'refs/tags/')
        with:
          files: |
            dist/*
          generate_release_notes: true
          draft: false
          prerelease: false
```

---

## 5. Required Repository Secrets & Setup Checklist

To enable publishing, the following configurations must be set up in the GitHub repository settings:

### 1. PyPI Trusted Publishing Setup
1. Log in to [pypi.org](https://pypi.org).
2. Go to **Publishing** -> **Add a publisher**.
3. Select **GitHub**.
4. Set Repository: `tensilestream/buildanchor`.
5. Set Workflow name: `release.yml`.
6. Set Environment name: `release-pypi`.
*No secret tokens needed.*

### 2. npm Secrets
* Register `@tensilestream` organization on npmjs.com.
* Generate an Automation Token on npmjs.com.
* Add repository secret: `NPM_TOKEN`.

### 3. Maven Central Secrets
* Register namespace `com.buildanchor` on [central.sonatype.com](https://central.sonatype.com/).
* Generate user deployment token.
* Add repository secrets:
  - `SONATYPE_CENTRAL_USERNAME`
  - `SONATYPE_CENTRAL_PASSWORD`
  - `GPG_PRIVATE_KEY`
  - `GPG_PASSPHRASE`

---

## 6. Implementation Phasing & Milestones

| Phase | Deliverable | Scope | Timeline |
|---|---|---|---|
| **Phase 1** | **PyPI & uv Live Release** | Wire PyPI Trusted Publisher and `.github/workflows/release.yml` for core package. | Immediate |
| **Phase 2** | **TypeScript SDK (`@tensilestream/buildanchor`)** | Scaffold `sdk/typescript` package with TS types, MCP client connector, and tests. | Next |
| **Phase 3** | **Maven Central Deployment** | Upgrade `sdk/java/pom.xml` with Javadoc, Sources, GPG signing plugins, and Sonatype Central connector. | Follow-up |
| **Phase 4** | **Homebrew Tap Automation** | Configure dispatch action to update `Formula/buildanchor.rb` upon PyPI tag release. | Follow-up |

---

## 7. Verification and Rollout Test Plan

1. **Dry Run Validation**: Run GitHub Actions `Release` workflow with `dry_run: true` on `workflow_dispatch`. Verify that all artifacts build cleanly without publishing.
2. **PyPI Test**: Verify installation via `uvx --from buildanchor buildanchor --version` and `pip install buildanchor`.
3. **npm Test**: Verify installation via `npx @tensilestream/buildanchor --version` and `npm install @tensilestream/buildanchor`.
4. **Maven Test**: Create a minimal test project and verify that Maven resolves `<groupId>com.buildanchor</groupId><artifactId>buildanchor-sdk</artifactId>`.

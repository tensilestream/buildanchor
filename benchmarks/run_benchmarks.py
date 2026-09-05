#!/usr/bin/env python3
"""BuildAnchor Token & Latency Benchmark Suite.

Generates 6 canonical ecosystem fixtures and benchmarks the token cost
and execution latency of BuildAnchor vs raw manifest injection into LLM context.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from buildanchor import BuildAnchor

# Token estimation fallback: 1 token ~= 4 chars (cl100k approximation)
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def create_fixtures(base: Path) -> dict[str, Path]:
    base.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, Path] = {}

    # 1. Maven Spring Boot 3 (Java 21) — realistic multi-dependency POM with plugins
    maven_dir = base / "maven-spring3"
    maven_dir.mkdir(parents=True, exist_ok=True)
    deps_xml = "\n".join([
        f"""        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-lib-{i}</artifactId>
            <version>3.4.2</version>
        </dependency>""" for i in range(1, 15)
    ])
    (maven_dir / "pom.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.4.2</version>
        <relativePath/>
    </parent>
    <groupId>com.example</groupId>
    <artifactId>enterprise-service</artifactId>
    <version>0.0.1-SNAPSHOT</version>
    <name>enterprise-service</name>
    <properties>
        <java.version>21</java.version>
        <spring-boot.version>3.4.2</spring-boot.version>
        <testcontainers.version>1.19.7</testcontainers.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency>
        <dependency>
            <groupId>jakarta.persistence</groupId>
            <artifactId>jakarta.persistence-api</artifactId>
            <version>3.1.0</version>
        </dependency>
        <dependency>
            <groupId>org.postgresql</groupId>
            <artifactId>postgresql</artifactId>
            <scope>runtime</scope>
        </dependency>
{deps_xml}
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <configuration>
                    <release>21</release>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
""", encoding="utf-8")
    src_java = maven_dir / "src/main/java/com/example/demo"
    src_java.mkdir(parents=True, exist_ok=True)
    (src_java / "User.java").write_text(
        """package com.example.demo;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class User {
    @Id private Long id;
    private String name;
}
""", encoding="utf-8")
    fixtures["maven-spring3"] = maven_dir

    # 2. Node.js ESM (TypeScript) — with full package-lock.json
    node_dir = base / "node-esm"
    node_dir.mkdir(parents=True, exist_ok=True)
    pkg_deps = {f"lib-module-{i}": f"^1.{i}.0" for i in range(1, 20)}
    pkg_deps.update({"fastify": "^4.26.0", "zod": "^3.22.4"})
    (node_dir / "package.json").write_text(
        json.dumps({
            "name": "node-esm-app",
            "version": "1.0.0",
            "type": "module",
            "engines": {"node": ">=20.0.0"},
            "scripts": {
                "build": "tsc",
                "test": "vitest run",
                "lint": "eslint ."
            },
            "dependencies": pkg_deps,
            "devDependencies": {
                "typescript": "^5.3.3",
                "vitest": "^1.2.2",
                "eslint": "^8.56.0"
            }
        }, indent=2), encoding="utf-8")
    lock_packages = {f"node_modules/pkg-{i}": {"version": f"1.{i}.0", "resolved": f"https://registry.npmjs.org/pkg-{i}/-/pkg-{i}-1.0.tgz"} for i in range(1, 40)}
    (node_dir / "package-lock.json").write_text(
        json.dumps({"name": "node-esm-app", "lockfileVersion": 3, "packages": lock_packages}, indent=2),
        encoding="utf-8"
    )
    (node_dir / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"target": "ES2022", "module": "NodeNext"}}, indent=2),
        encoding="utf-8"
    )
    src_node = node_dir / "src"
    src_node.mkdir(parents=True, exist_ok=True)
    (src_node / "index.ts").write_text(
        """import Fastify from 'fastify';
import { z } from 'zod';

const server = Fastify();
server.listen({ port: 8080 });
""", encoding="utf-8")
    fixtures["node-esm"] = node_dir

    # 3. Python FastAPI (uv / pyproject.toml + lockfile)
    py_dir = base / "python-pyproject"
    py_dir.mkdir(parents=True, exist_ok=True)
    py_deps = [f"\"dependency-pack-{i}>=1.{i}\"" for i in range(1, 25)]
    py_deps.extend(["\"fastapi>=0.110.0\"", "\"uvicorn>=0.28.0\"", "\"pydantic>=2.6.0\""])
    (py_dir / "pyproject.toml").write_text(
        f"""[project]
name = "fastapi-service"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    {', '.join(py_deps)}
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 88
""", encoding="utf-8")
    uv_lock_entries = "\n".join([f"[[package]]\nname = 'pack-{i}'\nversion = '1.{i}.0'\nsdist = {{}}\n" for i in range(1, 30)])
    (py_dir / "uv.lock").write_text(f"version = 1\nrequires-python = '>=3.11'\n{uv_lock_entries}", encoding="utf-8")
    src_py = py_dir / "src"
    src_py.mkdir(parents=True, exist_ok=True)
    (src_py / "app.py").write_text(
        """from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
""", encoding="utf-8")
    fixtures["python-pyproject"] = py_dir

    # 4. Go Module (Go 1.22 + go.sum)
    go_dir = base / "go-module"
    go_dir.mkdir(parents=True, exist_ok=True)
    go_reqs = "\n".join([f"\tgithub.com/example/lib-{i} v1.{i}.0" for i in range(1, 18)])
    (go_dir / "go.mod").write_text(
        f"""module github.com/example/api

go 1.22.0

require (
    github.com/gin-gonic/gin v1.9.1
    go.uber.org/zap v1.27.0
{go_reqs}
)
""", encoding="utf-8")
    go_sum_entries = "\n".join([f"github.com/example/lib-{i} v1.{i}.0 h1:checksum{i}=" for i in range(1, 18)])
    (go_dir / "go.sum").write_text(f"github.com/gin-gonic/gin v1.9.1 h1:4+0qZ...\n{go_sum_entries}\n", encoding="utf-8")
    (go_dir / "main.go").write_text(
        """package main

import (
    "github.com/gin-gonic/gin"
    "go.uber.org/zap"
)

func main() {
    r := gin.Default()
    r.Run()
}
""", encoding="utf-8")
    fixtures["go-module"] = go_dir

    # 5. Rust 2021 (Cargo.toml + Cargo.lock)
    rust_dir = base / "rust-2021"
    rust_dir.mkdir(parents=True, exist_ok=True)
    cargo_deps = "\n".join([f"crate-{i} = \"1.{i}\"" for i in range(1, 15)])
    (rust_dir / "Cargo.toml").write_text(
        f"""[package]
name = "rust-service"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = {{ version = "1.37", features = ["full"] }}
serde = {{ version = "1.0", features = ["derive"] }}
serde_json = "1.0"
{cargo_deps}
""", encoding="utf-8")
    lock_entries = "\n".join([
        f"[[package]]\nname = 'crate-{i}'\nversion = '1.{i}.0'\n" for i in range(1, 25)
    ])
    (rust_dir / "Cargo.lock").write_text(
        f"""version = 3

[[package]]
name = "rust-service"
version = "0.1.0"
dependencies = [
 "serde",
 "tokio",
]

[[package]]
name = "serde"
version = "1.0.198"

[[package]]
name = "tokio"
version = "1.37.0"

{lock_entries}
""", encoding="utf-8")
    src_rs = rust_dir / "src"
    src_rs.mkdir(parents=True, exist_ok=True)
    (src_rs / "main.rs").write_text(
        """use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize)]
struct Payload { id: u64 }

fn main() {}
""", encoding="utf-8")
    fixtures["rust-2021"] = rust_dir

    # 6. Polyglot (Node Frontend + Python Backend)
    poly_dir = base / "polyglot"
    poly_dir.mkdir(parents=True, exist_ok=True)
    (poly_dir / "package.json").write_text(
        json.dumps({
            "name": "polyglot-monorepo",
            "scripts": {"build": "npm run build:frontend", "test": "npm run test:ui"},
            "dependencies": {f"react-component-{i}": "^1.0.0" for i in range(1, 15)}
        }, indent=2), encoding="utf-8"
    )
    (poly_dir / "package-lock.json").write_text(
        json.dumps({"name": "polyglot-monorepo", "lockfileVersion": 3, "packages": {f"node_modules/rc-{i}": {"version": "1.0.0"} for i in range(1, 25)}}, indent=2),
        encoding="utf-8"
    )
    (poly_dir / "pyproject.toml").write_text(
        """[project]
name = "polyglot-backend"
requires-python = ">=3.10"
dependencies = [
    "flask>=3.0",
    "sqlalchemy>=2.0",
    "pydantic>=2.5",
]
[tool.pytest.ini_options]
""", encoding="utf-8"
    )
    (poly_dir / "Makefile").write_text("test:\n\tpytest && npm test\nbuild:\n\tnpm run build\n", encoding="utf-8")
    fixtures["polyglot"] = poly_dir

    return fixtures


def measure_raw_tokens(fixture_dir: Path) -> int:
    """Simulate naive agent loading: reading all configuration and manifest files."""
    total_text = ""
    for path in fixture_dir.rglob("*"):
        if path.is_file():
            if path.name in {"pom.xml", "package.json", "package-lock.json", "tsconfig.json", "pyproject.toml",
                             "uv.lock", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Makefile"}:
                total_text += path.read_text(encoding="utf-8", errors="replace") + "\n"
    return count_tokens(total_text)


def run_benchmarks() -> dict[str, Any]:
    fixtures = create_fixtures(FIXTURES_DIR)
    results = []

    print("Running BuildAnchor Benchmark Suite against 6 Canonical Fixtures...")
    print("=" * 80)
    print(f"{'Fixture':<20} | {'Raw Manifests':<14} | {'BuildAnchor':<12} | {'Reduction':<10} | {'Latency':<8}")
    print("-" * 80)

    total_raw = 0
    total_anchor = 0

    for name, path in fixtures.items():
        engine = BuildAnchor(path)

        # 1. Measure raw file tokens
        raw_tokens = measure_raw_tokens(path)
        total_raw += raw_tokens

        # 2. Measure BuildAnchor prompt generation & tokens
        start_time = time.perf_counter()
        block = engine.llm_prompt("Implement API endpoint")
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        anchor_tokens = block.token_estimate
        total_anchor += anchor_tokens

        reduction = round(((raw_tokens - anchor_tokens) / max(raw_tokens, 1)) * 100, 1)

        results.append({
            "fixture": name,
            "raw_manifest_tokens": raw_tokens,
            "buildanchor_tokens": anchor_tokens,
            "token_reduction_pct": reduction,
            "latency_ms": latency_ms,
            "build_systems": engine._inspect_cached().build_systems,
            "verified_test_cmd": engine.resolve_command("test").get("command"),
        })

        print(f"{name:<20} | {raw_tokens:>8} tokens | {anchor_tokens:>6} tokens | {reduction:>8}% | {latency_ms:>6} ms")

    print("=" * 80)
    overall_reduction = round(((total_raw - total_anchor) / max(total_raw, 1)) * 100, 1)
    print(f"{'TOTAL / AVERAGE':<20} | {total_raw:>8} tokens | {total_anchor:>6} tokens | {overall_reduction:>8}% |")
    print("=" * 80)

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_raw_tokens": total_raw,
        "total_buildanchor_tokens": total_anchor,
        "overall_token_reduction_pct": overall_reduction,
        "fixtures": results,
    }

    out_file = FIXTURES_DIR.parent / "results.json"
    out_file.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nSaved empirical benchmark results to: {out_file}")
    return output


if __name__ == "__main__":
    run_benchmarks()

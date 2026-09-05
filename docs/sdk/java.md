# Java SDK API reference

Use the Maven coordinate:

```text
io.github.tensilestream:buildanchor-sdk:1.0.0
```

The SDK requires Java 17, has no runtime dependencies, and uses a fixed
`ProcessBuilder` argument array for local CLI calls.

## Create a client

```java
import com.buildanchor.BuildAnchorClient;
import java.nio.file.Path;

try (BuildAnchorClient client = BuildAnchorClient.builder()
        .workspace(Path.of("."))
        .build()) {
    // Use the client.
}
```

The builder supports `workspace(Path)`, `endpoint(String)`, `token(String)`,
and `executable(String)`. Local mode is used unless `endpoint` is set. In HTTP
mode the configured workspace is included in every request and must be within
the server's allowed root.

## API

Every operation returns `BuildAnchorResponse`:

```java
public record BuildAnchorResponse(String operation, int statusCode, String json) {
    public boolean isSuccessful();
}
```

| Method | Purpose |
| --- | --- |
| `inspect()` | Full Build Truth report. |
| `context(int tokenBudget)` | Compact repository context. |
| `preflight(String objective, int tokenBudget)` | Compatibility and readiness gate. |
| `plan(String objective, int tokenBudget)` | Ordered implementation plan. |
| `changeImpact(String baseline)` | Git change-impact report. |
| `changeImpact(String baseline, boolean staged)` | Impact report, optionally for the Git index. |
| `validateChange(String baseline)` | Static validation with no project commands executed. |
| `validateChange(String baseline, boolean execute, int timeoutSeconds, boolean staged)` | Explicit static/probe validation control. |
| `validateChangeAsync(String baseline)` | Asynchronous validation using `CompletableFuture`. |
| `repairGuidance(String baseline)` | Structured repair actions. |
| `explainDependency(String dependency)` | Declared dependency evidence. |

## Typical agent flow

```java
String objective = "Add rate limiting to the API";
BuildAnchorResponse preflight = client.preflight(objective, 2500);
if (!preflight.isSuccessful()) {
    throw new IllegalStateException(preflight.json());
}

BuildAnchorResponse plan = client.plan(objective, 2500);
// Perform the planned edits, then validate without running project commands.
BuildAnchorResponse validation = client.validateChange("HEAD");
```

Execution is intentionally opt-in. Use `execute=true` only after authorizing
the repository's detected build/test command, and choose a bounded timeout:

```java
BuildAnchorResponse validation = client.validateChange("HEAD", true, 300, true);
```

## HTTP mode

```bash
buildanchor serve --workspace /path/to/repository --listen 127.0.0.1:8787
```

```java
BuildAnchorClient client = BuildAnchorClient.builder()
        .workspace(Path.of("."))
        .endpoint("http://127.0.0.1:8787")
        .token("optional-token")
        .build();
```

`statusCode` and the unmodified JSON body are preserved so callers can use
their preferred JSON library and apply application-specific error handling.

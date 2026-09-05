# BuildAnchor Java SDK

For the complete method reference, response contract, and HTTP examples, see
the [Java SDK API reference](../../docs/sdk/java.md).

Maven coordinates:

```text
io.github.tensilestream:buildanchor-sdk:1.1.6
```

Example:

```java
try (BuildAnchorClient client = BuildAnchorClient.builder()
        .workspace(Path.of("."))
        .build()) {
    BuildAnchorResponse baseline = client.inspect();
    BuildAnchorResponse preflight = client.preflight("Add a JPA entity", 2500);
    BuildAnchorResponse plan = client.plan("Add a JPA entity", 2500);
    // Static validation by default; execution must be explicitly enabled.
    BuildAnchorResponse result = client.validateChange("HEAD");
}
```

For staged changes or opt-in test execution, use the overload that makes the
execution decision explicit:

```java
BuildAnchorResponse result = client.validateChange("HEAD", true, 300, true);
```

The SDK uses Java 17 `HttpClient` and `ProcessBuilder` with a fixed argument
array. It has no runtime dependencies and preserves the canonical BuildAnchor
JSON response for typed application-level handling. In HTTP mode it includes
the configured workspace with every request, and the server enforces that the
workspace remains inside its allowed root.

# BuildAnchor Java SDK

Maven coordinates:

```text
com.buildanchor:buildanchor-sdk:0.1.0
```

Example:

```java
try (BuildAnchorClient client = BuildAnchorClient.builder()
        .workspace(Path.of("."))
        .build()) {
    BuildAnchorResponse baseline = client.inspect();
    BuildAnchorResponse preflight = client.preflight("Add a JPA entity", 2500);
    BuildAnchorResponse plan = client.plan("Add a JPA entity", 2500);
    BuildAnchorResponse result = client.validateChange("HEAD");
}
```

The SDK uses Java 17 `HttpClient` and `ProcessBuilder` with a fixed argument array. It has no runtime dependencies and preserves the canonical BuildAnchor JSON response for typed application-level handling.

// Copyright 2026 Tensilestream and BuildAnchor contributors
// SPDX-License-Identifier: Apache-2.0

package com.buildanchor;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;

/** Dependency-free Java 17 client for local or HTTP BuildAnchor deployments. */
public final class BuildAnchorClient implements AutoCloseable {
    private final Path workspace;
    private final String endpoint;
    private final String token;
    private final String executable;
    private final HttpClient http;

    private BuildAnchorClient(Builder builder) {
        this.workspace = builder.workspace;
        this.endpoint = builder.endpoint;
        this.token = builder.token;
        this.executable = builder.executable;
        this.http = HttpClient.newHttpClient();
    }

    public static Builder builder() {
        return new Builder();
    }

    public BuildAnchorResponse inspect() throws IOException, InterruptedException {
        return call("inspect", "{}", "/v1/inspect", "inspect");
    }

    public BuildAnchorResponse context(int tokenBudget) throws IOException, InterruptedException {
        return call("context", "{\"token_budget\":" + tokenBudget + "}", "/v1/context", "context", "--token-budget", Integer.toString(tokenBudget));
    }

    public BuildAnchorResponse preflight(String objective, int tokenBudget) throws IOException, InterruptedException {
        String body = "{\"objective\":" + quote(objective) + ",\"token_budget\":" + tokenBudget + "}";
        return call("preflight", body, "/v1/preflight", "preflight", "--token-budget", Integer.toString(tokenBudget));
    }

    public BuildAnchorResponse plan(String objective, int tokenBudget) throws IOException, InterruptedException {
        String body = "{\"objective\":" + quote(objective) + ",\"token_budget\":" + tokenBudget + "}";
        return call("plan", body, "/v1/plan", "plan", "--objective", objective, "--token-budget", Integer.toString(tokenBudget));
    }

    public BuildAnchorResponse changeImpact(String baseline) throws IOException, InterruptedException {
        return call("change-impact", jsonString("baseline", baseline), "/v1/change-impact", "change-impact", "--baseline", baseline);
    }

    public BuildAnchorResponse validateChange(String baseline) throws IOException, InterruptedException {
        return call("validate-change", jsonString("baseline", baseline), "/v1/validate-change", "validate-change", "--baseline", baseline);
    }

    public CompletableFuture<BuildAnchorResponse> validateChangeAsync(String baseline) {
        if (endpoint == null) {
            return CompletableFuture.supplyAsync(() -> {
                try {
                    return validateChange(baseline);
                } catch (Exception exception) {
                    throw new RuntimeException(exception);
                }
            });
        }
        HttpRequest request = request("/v1/validate-change", jsonString("baseline", baseline));
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString())
                .thenApply(response -> new BuildAnchorResponse("validate-change", response.statusCode(), response.body()));
    }

    public BuildAnchorResponse repairGuidance(String baseline) throws IOException, InterruptedException {
        return call("repair-guidance", jsonString("baseline", baseline), "/v1/repair-guidance", "repair", "--baseline", baseline);
    }

    public BuildAnchorResponse explainDependency(String dependency) throws IOException, InterruptedException {
        return call("explain-dependency", jsonString("dependency", dependency), "/v1/explain-dependency", "explain-dependency", "--dependency", dependency);
    }

    private BuildAnchorResponse call(String operation, String body, String path, String... localArgs) throws IOException, InterruptedException {
        if (endpoint == null) {
            String[] command = new String[localArgs.length + 5];
            command[0] = executable;
            System.arraycopy(localArgs, 0, command, 1, localArgs.length);
            command[localArgs.length + 1] = "--workspace";
            command[localArgs.length + 2] = workspace.toString();
            command[localArgs.length + 3] = "--format";
            command[localArgs.length + 4] = "json";
            Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
            String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            return new BuildAnchorResponse(operation, process.waitFor() == 0 ? 200 : 400, output);
        }
        HttpResponse<String> response = http.send(request(path, body), HttpResponse.BodyHandlers.ofString());
        return new BuildAnchorResponse(operation, response.statusCode(), response.body());
    }

    private HttpRequest request(String path, String body) {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(endpoint + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body));
        if (token != null) {
            request.header("Authorization", "Bearer " + token);
        }
        return request.build();
    }

    private static String jsonString(String key, String value) {
        String escaped = quote(value);
        return "{\"" + key + "\":" + escaped + "}";
    }

    private static String quote(String value) {
        String escaped = value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
        return "\"" + escaped + "\"";
    }

    @Override
    public void close() {
        // HttpClient owns no closeable resources in this dependency-free client.
    }

    public static final class Builder {
        private Path workspace = Path.of(".").toAbsolutePath().normalize();
        private String endpoint;
        private String token;
        private String executable = "buildanchor";

        public Builder workspace(Path value) { workspace = Objects.requireNonNull(value); return this; }
        public Builder endpoint(String value) { endpoint = value == null ? null : value.replaceAll("/$", ""); return this; }
        public Builder token(String value) { token = value; return this; }
        public Builder executable(String value) { executable = Objects.requireNonNull(value); return this; }
        public BuildAnchorClient build() { return new BuildAnchorClient(this); }
    }
}

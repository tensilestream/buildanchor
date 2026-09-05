// Copyright 2026 Tensilestream and BuildAnchor contributors
// SPDX-License-Identifier: Apache-2.0

package com.buildanchor;

/** Immutable response wrapper. JSON is preserved exactly for application-specific parsing. */
public record BuildAnchorResponse(String operation, int statusCode, String json) {
    public boolean isSuccessful() {
        return statusCode >= 200 && statusCode < 300;
    }
}

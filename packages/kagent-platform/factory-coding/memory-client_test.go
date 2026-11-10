package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type fixtureMemory struct {
	called bool
	err    error
}

func (backend *fixtureMemory) Add(_ context.Context, _ memoryRequest) (string, error) {
	backend.called = true
	return "memory-1", backend.err
}
func (backend *fixtureMemory) Search(_ context.Context, _ memoryRequest) ([]map[string]any, error) {
	backend.called = true
	return []map[string]any{{"id": "memory-1", "content": "finding"}}, backend.err
}

func invokeMemory(backend *fixtureMemory, input map[string]any, address string) *httptest.ResponseRecorder {
	body, _ := json.Marshal(input)
	request := httptest.NewRequest(http.MethodPost, "/memory/add", bytes.NewReader(body))
	request.RemoteAddr = address
	writer := httptest.NewRecorder()
	memoryHandler(backend).ServeHTTP(writer, request)
	return writer
}

func TestMemoryBridgeRejectsForeignScopeAndNonLocalCaller(t *testing.T) {
	for _, item := range []struct {
		address string
		foreign bool
	}{{"192.0.2.10:12345", false}, {"127.0.0.1:12345", true}} {
		backend := &fixtureMemory{}
		input := map[string]any{"content": "finding", "vector": make([]float32, 768)}
		if item.foreign {
			input["userId"] = "different-user"
		}
		response := invokeMemory(backend, input, item.address)
		if response.Code < 400 || backend.called {
			t.Fatalf("foreign scope/caller reached native memory: %d", response.Code)
		}
	}
}

func TestMemoryBridgeAcceptsScopedRequestAndSanitizesDependencyFailures(t *testing.T) {
	for _, failure := range []error{nil, errors.New("secret-diagnostic"), status.Error(codes.PermissionDenied, "private-details")} {
		backend := &fixtureMemory{err: failure}
		response := invokeMemory(backend, map[string]any{"content": "finding", "vector": make([]float32, 768)}, "127.0.0.1:12345")
		if !backend.called {
			t.Fatal("valid memory request did not reach native service")
		}
		if failure == nil && response.Code != http.StatusOK {
			t.Fatalf("valid memory failed: %d", response.Code)
		}
		if failure != nil && (response.Code < 400 || strings.Contains(response.Body.String(), failure.Error())) {
			t.Fatal("dependency failure leaked details or was reported successful")
		}
	}
}

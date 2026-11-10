package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"time"

	"github.com/kagent-dev/kagent/go/adk/pkg/controllerclient"
	runtimev1 "github.com/kagent-dev/kagent/go/api/gen/kagent/runtime/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

type memoryRequest struct {
	Content  string            `json:"content"`
	Vector   []float32         `json:"vector"`
	Metadata map[string]string `json:"metadata"`
	TTLDays  int32             `json:"ttlDays"`
	Limit    int32             `json:"limit"`
	MinScore float64           `json:"minScore"`
}

type nativeMemoryBackend interface {
	Add(context.Context, memoryRequest) (string, error)
	Search(context.Context, memoryRequest) ([]map[string]any, error)
}

type controllerMemory struct{ client *controllerclient.Client }

func (backend controllerMemory) Add(ctx context.Context, input memoryRequest) (string, error) {
	ctx, cancel := backend.client.CallContext(ctx, "")
	defer cancel()
	values := map[string]any{"reviewStatus": "candidate"}
	for _, key := range []string{"repository", "revision", "sourceTaskId"} {
		if value := input.Metadata[key]; value != "" {
			values[key] = value
		}
	}
	metadata, err := structpb.NewStruct(values)
	if err != nil {
		return "", err
	}
	response, err := backend.client.MemoryService().AddSession(ctx, &runtimev1.MemoryServiceAddSessionRequest{
		Memory: &runtimev1.SessionMemoryInput{Content: input.Content, Vector: input.Vector, Metadata: metadata, TtlDays: &input.TTLDays},
	})
	if err != nil {
		return "", err
	}
	return response.GetId(), nil
}

func (backend controllerMemory) Search(ctx context.Context, input memoryRequest) ([]map[string]any, error) {
	ctx, cancel := backend.client.CallContext(ctx, "")
	defer cancel()
	response, err := backend.client.MemoryService().Search(ctx, &runtimev1.MemoryServiceSearchRequest{
		Vector: input.Vector, Limit: &input.Limit, MinScore: &input.MinScore,
	})
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(response.GetMemories()))
	for _, item := range response.GetMemories() {
		result = append(result, map[string]any{"id": item.GetId(), "content": item.GetContent(), "score": item.GetScore(), "metadata": item.GetMetadata().AsMap()})
	}
	return result, nil
}

func memoryHandler(backend nativeMemoryBackend) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.Header().Set("Cache-Control", "no-store")
		reply := func(code int, body any) {
			writer.WriteHeader(code)
			_ = json.NewEncoder(writer).Encode(body)
		}
		host, _, err := net.SplitHostPort(request.RemoteAddr)
		if err != nil || !net.ParseIP(host).IsLoopback() || request.Header.Get("Origin") != "" {
			reply(http.StatusForbidden, map[string]string{"error": "memory_loopback_only"})
			return
		}
		if request.Method != http.MethodPost || (request.URL.Path != "/memory/add" && request.URL.Path != "/memory/search") {
			reply(http.StatusNotFound, map[string]string{"error": "memory_route_not_found"})
			return
		}
		var input memoryRequest
		decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 128*1024))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&input); err != nil || len(input.Vector) != 768 {
			reply(http.StatusBadRequest, map[string]string{"error": "memory_request_invalid"})
			return
		}
		if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
			reply(http.StatusBadRequest, map[string]string{"error": "memory_request_invalid"})
			return
		}
		for _, value := range input.Vector {
			if math.IsNaN(float64(value)) || math.IsInf(float64(value), 0) {
				reply(http.StatusBadRequest, map[string]string{"error": "memory_vector_invalid"})
				return
			}
		}
		if input.TTLDays == 0 {
			input.TTLDays = 15
		}
		if input.Limit == 0 {
			input.Limit = 5
		}
		if input.TTLDays < 1 || input.TTLDays > 365 || input.Limit < 1 || input.Limit > 20 || input.MinScore < 0 || input.MinScore > 1 {
			reply(http.StatusBadRequest, map[string]string{"error": "memory_limits_invalid"})
			return
		}
		if request.URL.Path == "/memory/add" {
			if input.Content == "" || len(input.Content) > 32768 {
				reply(http.StatusBadRequest, map[string]string{"error": "memory_content_invalid"})
				return
			}
			identifier, err := backend.Add(request.Context(), input)
			if err != nil {
				reply(memoryStatus(err), map[string]string{"error": "native_memory_unavailable"})
				return
			}
			reply(http.StatusOK, map[string]string{"id": identifier})
			return
		}
		items, err := backend.Search(request.Context(), input)
		if err != nil {
			reply(memoryStatus(err), map[string]string{"error": "native_memory_unavailable"})
			return
		}
		reply(http.StatusOK, map[string]any{"memories": items})
	})
}

func memoryStatus(err error) int {
	switch status.Code(err) {
	case codes.Unauthenticated:
		return http.StatusUnauthorized
	case codes.PermissionDenied:
		return http.StatusForbidden
	case codes.InvalidArgument:
		return http.StatusBadRequest
	default:
		return http.StatusServiceUnavailable
	}
}

func startMemoryBridge(ctx context.Context, client *controllerclient.Client, address string) (func(), error) {
	host, _, err := net.SplitHostPort(address)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return nil, fmt.Errorf("memory bridge requires an explicit loopback address")
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return nil, err
	}
	server := &http.Server{Handler: memoryHandler(controllerMemory{client}), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			_ = server.Close()
		}
	}()
	stop := func() { _ = server.Close() }
	go func() {
		<-ctx.Done()
		stop()
	}()
	return stop, nil
}

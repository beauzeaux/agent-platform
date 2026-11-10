package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
	"time"

	"github.com/kagent-dev/kagent/go/adk/pkg/controllerclient"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

func archiveHTTPHandler(controller *controllerclient.Client, connection grpc.ClientConnInterface) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.Header().Set("Cache-Control", "no-store")
		host, _, err := net.SplitHostPort(request.RemoteAddr)
		if err != nil || !net.ParseIP(host).IsLoopback() || request.Header.Get("Origin") != "" {
			http.Error(writer, `{"error":"archive_loopback_only"}`, http.StatusForbidden)
			return
		}
		if request.Method != http.MethodPost || request.URL.Path != "/archive/export" {
			http.Error(writer, `{"error":"archive_route_not_found"}`, http.StatusNotFound)
			return
		}
		var body map[string]any
		if err := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 6*1024*1024)).Decode(&body); err != nil {
			http.Error(writer, `{"error":"archive_request_invalid"}`, http.StatusBadRequest)
			return
		}
		input, err := structpb.NewStruct(body)
		if err != nil {
			http.Error(writer, `{"error":"archive_request_invalid"}`, http.StatusBadRequest)
			return
		}
		ctx, cancel := controller.CallContext(request.Context(), "")
		defer cancel()
		output := new(structpb.Struct)
		if err := connection.Invoke(ctx, archiveMethod, input, output); err != nil {
			code := http.StatusServiceUnavailable
			switch status.Code(err) {
			case codes.Unauthenticated:
				code = http.StatusUnauthorized
			case codes.PermissionDenied:
				code = http.StatusForbidden
			case codes.InvalidArgument:
				code = http.StatusBadRequest
			}
			http.Error(writer, `{"error":"archive_export_pending"}`, code)
			return
		}
		_ = json.NewEncoder(writer).Encode(output.AsMap())
	})
}

func startArchiveBridge(ctx context.Context, controller *controllerclient.Client, apiURL, address string) (func(), error) {
	target, err := url.Parse(apiURL)
	if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") ||
		(target.Path != "" && target.Path != "/") || target.RawQuery != "" || target.Fragment != "" || target.User != nil {
		return nil, errors.New("invalid archive controller origin")
	}
	transport := insecure.NewCredentials()
	if target.Scheme == "https" {
		transport = credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})
	}
	connection, err := grpc.NewClient("passthrough:///"+target.Host, grpc.WithTransportCredentials(transport))
	if err != nil {
		return nil, errors.New("archive controller client unavailable")
	}
	host, _, err := net.SplitHostPort(address)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		connection.Close()
		return nil, errors.New("archive bridge requires explicit loopback")
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		connection.Close()
		return nil, err
	}
	server := &http.Server{Handler: archiveHTTPHandler(controller, connection), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	go func() { _ = server.Serve(listener) }()
	stop := func() { _ = server.Close(); _ = connection.Close() }
	if done := ctx.Done(); done != nil {
		go func() { <-done; stop() }()
	}
	return stop, nil
}

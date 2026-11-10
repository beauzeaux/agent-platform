package main

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/baggage"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

func TestTransportStartupWaitsForSuccessfulReadinessAndHonorsCancellation(t *testing.T) {
	code := http.StatusServiceUnavailable
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(code)
	}))
	defer server.Close()
	if transportReady(context.Background(), server.URL) {
		t.Fatal("unready credential transport admitted")
	}
	code = http.StatusNoContent
	if !transportReady(context.Background(), server.URL) {
		t.Fatal("ready credential transport rejected")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if transportReady(ctx, server.URL) {
		t.Fatal("canceled transport startup admitted")
	}
}

func TestCodingCommandSelectsInheritedAgentAdapterWithoutShell(t *testing.T) {
	t.Setenv("AGENCY_AGENT_EXECUTABLE", "/usr/bin/dotnet")
	t.Setenv("AGENCY_AGENT_ENTRYPOINT", "/app/agency-coder/AgencyCoder.dll")
	t.Setenv("AGENCY_CODER_EXECUTABLE", "/legacy/node")
	t.Setenv("AGENCY_CODER_ENTRYPOINT", "/legacy/agent.mjs")
	command := codingCommand()
	if command.Path != "/usr/bin/dotnet" || len(command.Args) != 2 ||
		command.Args[1] != "/app/agency-coder/AgencyCoder.dll" {
		t.Fatal("derived image adapter was not selected directly")
	}
}

func TestCodingCommandPreservesExistingSessionRecovery(t *testing.T) {
	t.Setenv("AGENCY_AGENT_EXECUTABLE", "")
	t.Setenv("AGENCY_AGENT_ENTRYPOINT", "")
	t.Setenv("AGENCY_CODER_EXECUTABLE", "/usr/bin/dotnet")
	t.Setenv("AGENCY_CODER_ENTRYPOINT", "/app/agency-coder/AgencyCoder.dll")
	command := codingCommand()
	if command.Path != "/usr/bin/dotnet" || len(command.Args) != 2 ||
		command.Args[1] != "/app/agency-coder/AgencyCoder.dll" {
		t.Fatal("published adapter was not selected directly")
	}
	t.Setenv("AGENCY_CODER_EXECUTABLE", "")
	t.Setenv("AGENCY_CODER_ENTRYPOINT", "")
	command = codingCommand()
	if command.Path != "/usr/local/bin/node" || command.Args[1] != "/app/coder/agent.mjs" {
		t.Fatal("legacy recovery command changed")
	}
}

func TestCodingRequestAllowsRepositoryWorkingDirectoryButRejectsUnknownScope(t *testing.T) {
	if !supportedCodingRequestField("workingDirectory") {
		t.Fatal("working directory field was not propagated to the adapter")
	}
	if supportedCodingRequestField("repositoryUrl") {
		t.Fatal("caller-selected repository was admitted")
	}
}

func TestCodingBridgeInjectsW3CTraceAndBaggage(t *testing.T) {
	previous := otel.GetTextMapPropagator()
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{}))
	t.Cleanup(func() { otel.SetTextMapPropagator(previous) })
	traceID, _ := trace.TraceIDFromHex("11111111111111111111111111111111")
	spanID, _ := trace.SpanIDFromHex("1111111111111111")
	ctx := trace.ContextWithSpanContext(context.Background(), trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: traceID, SpanID: spanID, TraceFlags: trace.FlagsSampled,
	}))
	member, err := baggage.NewMember("tenant", "test")
	if err != nil {
		t.Fatal(err)
	}
	bag, err := baggage.New(member)
	if err != nil {
		t.Fatal(err)
	}
	request, _ := http.NewRequest(http.MethodPost, "http://127.0.0.1:18080/tasks", nil)
	injectTraceContext(baggage.ContextWithBaggage(ctx, bag), request)
	if got := request.Header.Get("traceparent"); got !=
		"00-11111111111111111111111111111111-1111111111111111-01" {
		t.Fatalf("unexpected traceparent %q", got)
	}
	if got := request.Header.Get("baggage"); got != "tenant=test" {
		t.Fatalf("unexpected baggage %q", got)
	}
}

func TestCodingChildReceivesCompiledFileAndLiveIdentityWithoutInlineConfig(t *testing.T) {
	coder := &codingRuntime{compiledConfigPath: "/config/config.json", agentName: "native-coder",
		agentNamespace: "factory-executions"}
	environment := coder.childEnvironment([]string{
		"AGENT_NATIVE_CONFIG=true", "OPENAI_API_KEY=kagent-credential-injected",
		"KAGENT_CONFIG_JSON=must-not-copy-inline", "AGENT_COMPILED_CONFIG_PATH=/unvalidated/config",
		"AGENT_SESSION_ID=inherited-session", "AGENT_RUNTIME_IDENTITY_PATH=/inherited/identity",
	}, "/data/coding", "live-session")
	values := map[string]string{}
	for _, entry := range environment {
		key, value, _ := strings.Cut(entry, "=")
		if _, duplicate := values[key]; duplicate {
			t.Fatalf("duplicate child environment variable %s", key)
		}
		values[key] = value
	}
	if _, copied := values["KAGENT_CONFIG_JSON"]; copied {
		t.Fatal("inline compiled configuration copied to child")
	}
	for key, expected := range map[string]string{
		"AGENT_NATIVE_CONFIG": "true", "OPENAI_API_KEY": "kagent-credential-injected",
		"AGENT_COMPILED_CONFIG_PATH":  "/config/config.json",
		"AGENT_RUNTIME_IDENTITY_PATH": "/run/kagent/identity/name",
		"AGENT_SESSION_ID":            "live-session", "AGENT_KAGENT_NAME": "native-coder",
		"AGENT_KAGENT_NAMESPACE": "factory-executions",
	} {
		if values[key] != expected {
			t.Fatalf("incorrect child contract for %s", key)
		}
	}
}

func TestCodingSubmissionRetriesOnlyExplicitPreflightRejection(t *testing.T) {
	for _, test := range []struct {
		name      string
		body      string
		wantCalls int
		wantError bool
	}{
		{"proven rejection", `{"error":"agency_plugin_preflight_failed"}`, 2, false},
		{"repository rejection", `{"error":"repository_preflight_failed"}`, 2, false},
		{"unknown rejection", `{"error":"outcome_unknown"}`, 1, true},
		{"malformed response", `not JSON`, 1, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			previous := http.DefaultTransport
			transport := &http.Transport{Proxy: http.ProxyURL(&url.URL{Scheme: "http", Host: listener.Addr().String()})}
			http.DefaultTransport = transport
			defer func() { http.DefaultTransport = previous; transport.CloseIdleConnections() }()
			var calls atomic.Int32
			server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls.Add(1) == 1 {
					w.WriteHeader(http.StatusServiceUnavailable)
					_, _ = w.Write([]byte(test.body))
					return
				}
				_, _ = w.Write([]byte(`{"status":"accepted"}`))
			})}
			go func() { _ = server.Serve(listener) }()
			defer server.Close()
			err = submitCoding(context.Background(), map[string]string{"id": "stable"})
			if (err != nil) != test.wantError || int(calls.Load()) != test.wantCalls {
				t.Fatal("unexpected submission/replay", calls.Load(), err)
			}
		})
	}
}

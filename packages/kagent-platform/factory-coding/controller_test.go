package main

import (
	"context"
	"net/http"
	"net/url"
	"testing"

	"github.com/kagent-dev/kagent/go/core/pkg/auth"
	"github.com/kagent-dev/kagent/go/core/pkg/egresspolicy"
	ateapi "go.ate.dev/substrate/api/ateapi/v1"
)

func TestTrustedCredentialAndIdentity(t *testing.T) {
	authenticator := trustedAuthenticator{token: "test-protected-token"}
	for _, headers := range []http.Header{{}, {"X-User-Id": {"factory-adapter"}},
		{"Authorization": {"Bearer wrong"}}, {"Authorization": {"Bearer test-protected-token", "Bearer test-protected-token"}}} {
		if _, err := authenticator.Authenticate(context.Background(), headers, url.Values{"user_id": {"factory-adapter"}}); err == nil {
			t.Fatal("unverified identity accepted")
		}
	}
	session, err := authenticator.Authenticate(context.Background(), http.Header{"Authorization": {"Bearer test-protected-token"}}, nil)
	if err != nil || session.Principal().ID != "factory-adapter" {
		t.Fatal(session, err)
	}
	request, _ := http.NewRequest("POST", "http://runtime", nil)
	request.Header.Set("Authorization", "Bearer test-protected-token")
	if err := authenticator.UpstreamAuth(request, session, "session"); err != nil || request.Header.Get("Authorization") != "" {
		t.Fatal("management credential forwarded to guest")
	}
}

func TestFactoryScopeAndProxyCompiler(t *testing.T) {
	authorizer := factoryAuthorizer{}
	for _, principal := range []auth.Principal{{ID: "untrusted"}, {ID: "spoofed-actor"}} {
		if err := authorizer.Check(context.Background(), principal, auth.VerbCreate, auth.Resource{Namespace: "factory-executions"}); err == nil {
			t.Fatal("untrusted principal authorized")
		}

	}
	if err := authorizer.Check(context.Background(), auth.Principal{ID: "factory-adapter"}, auth.VerbCreate, auth.Resource{Namespace: "kagent"}); err == nil {
		t.Fatal("original control-plane scope authorized")
	}
	policy := &ateapi.EgressPolicy{}
	compiler := proxyCompiler{}
	if err := compiler.Compile(context.Background(), egresspolicy.Input{Atespace: "kagent"}, policy); err == nil || len(policy.Rules) != 0 {
		t.Fatal("unexpected space received privileged egress")
	}
	if err := compiler.Compile(context.Background(), egresspolicy.Input{Atespace: "factory-executions"}, policy); err != nil {
		t.Fatal(err)
	}
	if len(policy.Rules) != 2 || policy.Rules[0].GetHttps().GetEffects().GetReplaceHeaders()[0].GetActorJwt().Audiences[0] != "https://agent-proxies.proxy-access.svc/session" {
		t.Fatal("unexpected host proxy credential binding")
	}
}

func TestExplicitWorkloadNamespacesPermitSessionCreationAndProxyCompilation(t *testing.T) {
	for _, namespace := range []string{"baseline", "cluster-management", "factory-executions"} {
		if err := (factoryAuthorizer{}).Check(context.Background(), auth.Principal{ID: "factory-adapter"},
			auth.VerbCreate, auth.Resource{Namespace: namespace, Type: "Session"}); err != nil {
			t.Fatalf("enrolled namespace %s denied: %v", namespace, err)
		}
		policy := &ateapi.EgressPolicy{}
		if err := (proxyCompiler{}).Compile(context.Background(), egresspolicy.Input{Atespace: namespace}, policy); err != nil ||
			len(policy.Rules) != 2 {
			t.Fatalf("enrolled namespace %s did not receive required proxy rules", namespace)
		}
	}
	if err := (factoryAuthorizer{}).Check(context.Background(), auth.Principal{ID: "factory-adapter"},
		auth.VerbCreate, auth.Resource{Namespace: "unmanaged", Type: "Session"}); err == nil {
		t.Fatal("unmanaged namespace authorized")
	}
}

func TestRuntimeMemoryAuthorizationKeepsFactoryNamespaceBoundary(t *testing.T) {
	principal := auth.Principal{RuntimeBinding: &auth.RuntimeBinding{SessionID: "owned-session", CreatorID: "owner"}}
	authorizer := factoryAuthorizer{}
	if err := authorizer.Check(context.Background(), principal, auth.VerbCreate,
		auth.Resource{Type: "Memory", Name: "factory-executions/factory-coder"}); err != nil {
		t.Fatal("verified runtime cannot use its factory memory")
	}
	for _, resource := range []auth.Resource{
		{Type: "Memory", Name: "kagent/other"},
		{Type: "Memory", Name: "factory-executions/"},
		{Type: "Session", Name: "factory-executions/factory-coder"},
		{Type: "Memory", Namespace: "other", Name: "factory-executions/factory-coder"},
	} {
		if err := authorizer.Check(context.Background(), principal, auth.VerbCreate, resource); err == nil {
			t.Fatal("runtime crossed factory authorization boundary")
		}
	}
}

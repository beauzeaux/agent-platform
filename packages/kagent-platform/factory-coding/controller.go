package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/kagent-dev/kagent/go/api/authorization"
	configv1 "github.com/kagent-dev/kagent/go/api/gen/kagent/config/v1"
	"github.com/kagent-dev/kagent/go/core/pkg/app"
	"github.com/kagent-dev/kagent/go/core/pkg/auth"
	"github.com/kagent-dev/kagent/go/core/pkg/config"
	"github.com/kagent-dev/kagent/go/core/pkg/egresspolicy"
	ateapi "go.ate.dev/substrate/api/ateapi/v1"
)

type trustedSession struct{}

func (trustedSession) Principal() auth.Principal { return auth.Principal{ID: "factory-adapter"} }

type trustedAuthenticator struct{ token string }

func (a trustedAuthenticator) Authenticate(_ context.Context, headers http.Header, _ url.Values) (auth.Session, error) {
	if len(headers.Values("Authorization")) != 1 || subtle.ConstantTimeCompare([]byte(headers.Get("Authorization")), []byte("Bearer "+a.token)) != 1 {
		return nil, errors.New("unauthenticated")
	}
	return trustedSession{}, nil
}
func (trustedAuthenticator) UpstreamAuth(request *http.Request, _ auth.Session, _ string) error {
	request.Header.Del("Authorization")
	return nil
}

type factoryAuthorizer struct{}

func managedNamespace(namespace string) bool {
	return namespace == "baseline" || namespace == "cluster-management" || namespace == "factory-executions"
}

func (factoryAuthorizer) Check(_ context.Context, principal auth.Principal, _ auth.Verb, resource auth.Resource) error {
	if principal.ID == "" && principal.RuntimeBinding == nil {
		return nil
	}
	if principal.RuntimeBinding != nil && resource.Type == "Memory" {
		namespace, name, found := strings.Cut(resource.Name, "/")
		if found && namespace == "factory-executions" && name != "" && !strings.Contains(name, "/") &&
			(resource.Namespace == "" || resource.Namespace == namespace) {
			return nil
		}
		return errors.New("factory memory scope denied")
	}
	if principal.ID != "factory-adapter" || resource.Namespace != "" && !managedNamespace(resource.Namespace) {
		return errors.New("factory resource access denied")
	}
	return nil
}
func (factoryAuthorizer) Scope(_ context.Context, principal auth.Principal, _ auth.Verb, _ string) (authorization.AuthorizationScope, error) {
	if principal.ID != "factory-adapter" {
		return authorization.AuthorizationScope{}, errors.New("factory collection denied")
	}
	return authorization.AuthorizationScope{Kind: authorization.ScopeAll}, nil
}

type proxyCompiler struct{}

func (proxyCompiler) Compile(_ context.Context, input egresspolicy.Input, policy *ateapi.EgressPolicy) error {
	if !managedNamespace(input.Atespace) {
		return errors.New("unexpected execution space")
	}
	host := "agent-proxies.proxy-access.svc.cluster.local"
	policy.Rules = append(policy.Rules,
		&ateapi.EgressRule{Https: &ateapi.HttpsRule{Hostnames: []string{host}, Ports: &ateapi.Ports{Numbers: []int32{8443}},
			Effects: &ateapi.HttpRuleEffects{ReplaceHeaders: []*ateapi.CredentialHeader{{Header: "authorization", Prefix: "Bearer ",
				ActorJwt: &ateapi.ActorJwtSource{Audiences: []string{"https://agent-proxies.proxy-access.svc/session"}, ExpirationSeconds: 300}}}}}},
		&ateapi.EgressRule{TlsPassthrough: &ateapi.TlsPassthroughRule{Hostnames: []string{host}, Ports: &ateapi.Ports{Numbers: []int32{8444}}}},
	)
	return nil
}

func main() {
	if len(os.Args) == 3 && os.Args[1] == "--archive-prepare" {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := prepareArchiveStorage(ctx, os.Args[2]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Println("Private archive bucket ready.")
		return
	}
	file := flag.String("config", "/etc/kagent/controller.yaml", "controller config")
	flag.Parse()
	cfg := &configv1.ControllerConfig{}
	if err := config.Load(*file, cfg); err != nil {
		panic(err)
	}
	config.DefaultController(cfg)
	config.ResolveControllerPaths(cfg, *file)
	token, err := os.ReadFile("/run/factory/controller-token")
	if err != nil || len(strings.TrimSpace(string(token))) < 32 {
		panic("protected controller token required")
	}
	if err := app.SetupLogger(cfg.GetLogging().GetLevel()); err != nil {
		panic(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	archiveServices, archivePolicies, closeArchive, err := configureArchive(ctx)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer closeArchive()
	if err := app.Run(ctx, cfg, app.Options{Authenticator: trustedAuthenticator{strings.TrimSpace(string(token))},
		Authorizer: factoryAuthorizer{}, EgressPolicyCompilers: []egresspolicy.Compiler{proxyCompiler{}},
		GRPCServices: archiveServices, MethodPolicies: archivePolicies}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

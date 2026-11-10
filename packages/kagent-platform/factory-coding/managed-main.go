package main

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2asrv"
	"github.com/kagent-dev/kagent/go/adk/pkg/app"
	"github.com/kagent-dev/kagent/go/adk/pkg/config"
	"github.com/kagent-dev/kagent/go/adk/pkg/controllerclient"
	apia2a "github.com/kagent-dev/kagent/go/api/a2a"
	"github.com/kagent-dev/kagent/go/pkg/logging"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

type codingRuntime struct {
	mu                 sync.Mutex
	child              *exec.Cmd
	session            string
	done               chan struct{}
	transportDone      chan struct{}
	compiledConfigPath string
	agentName          string
	agentNamespace     string
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--harmless-child" {
		harmlessChild()
		return
	}
	if err := codingMain(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func codingMain() error {
	if err := config.MaterializeFromEnv("/config"); err != nil {
		return err
	}
	compiled, err := config.LoadAgentConfig("/config/config.json")
	if err != nil {
		return err
	}
	runtime := config.ProcessConfig(compiled)
	logger, err := logging.New(os.Stderr, cmp.Or(runtime.LogLevel, "info"))
	if err != nil {
		return err
	}
	e, err := openEngine("/data", apia2a.RuntimeIdentityPath)
	if err != nil {
		return err
	}
	coder := &codingRuntime{compiledConfigPath: "/config/config.json",
		agentName: compiled.GetRuntime().GetName(), agentNamespace: compiled.GetRuntime().GetNamespace()}
	e.performCommand = coder.perform
	controller, err := controllerclient.New(controllerclient.Config{
		APIURL: runtime.ControlPlaneUrl, AgentName: cmp.Or(runtime.Name, "factory-coder"),
	})
	if err != nil {
		return err
	}
	defer controller.Close()
	if os.Getenv("NATIVE_MEMORY_ENABLED") == "true" {
		stopMemory, err := startMemoryBridge(context.Background(), controller, "127.0.0.1:18082")
		if err != nil {
			return err
		}
		defer stopMemory()
	}
	if os.Getenv("SESSION_ARCHIVE_EXPORT_ENABLED") == "true" {
		stopArchive, err := startArchiveBridge(context.Background(), controller, runtime.ControlPlaneUrl, "127.0.0.1:18083")
		if err != nil {
			return err
		}
		defer stopArchive()
	}
	application, err := newApplication(app.AppConfig{
		ControllerClient: controller, MaxContentLength: runtime.MaxContentLength,
		AgentCard: a2a.AgentCard{Name: "factory-coder", Version: "1",
			Capabilities:        a2a.AgentCapabilities{Streaming: true},
			SupportedInterfaces: []*a2a.AgentInterface{a2a.NewAgentInterface("/", a2a.TransportProtocolGRPC)}},
		Port: "80", AppName: cmp.Or(runtime.Name, "factory-coder"), Logger: logger,
	}, e)
	if err != nil {
		return err
	}
	return application.Run()
}

func (c *codingRuntime) start(ctx context.Context, session string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.child != nil {
		if c.session != session {
			return errors.New("coding process belongs to another session")
		}
		select {
		case <-c.done:
			c.stopLocked()
		default:
			return nil
		}
	}
	directory := "/data/coding"
	if err := os.MkdirAll(directory, 0700); err != nil {
		return err
	}
	owner := filepath.Join(directory, "session-owner")
	data, err := os.ReadFile(owner)
	if err == nil && string(data) != session {
		return errors.New("inherited coding workspace rejected")
	}
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.WriteFile(owner, []byte(session), 0600); err != nil {
		return err
	}
	child := codingCommand()
	child.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
	child.Env = c.childEnvironment(os.Environ(), directory, session)
	child.Stdout, child.Stderr = os.Stderr, os.Stderr
	if err := child.Start(); err != nil {
		return err
	}
	c.child, c.session, c.done = child, session, make(chan struct{})
	done := c.done
	go func() { _ = child.Wait(); close(done) }()
	if executable := os.Getenv("AGENCY_TRANSPORT_EXECUTABLE"); executable != "" {
		transport := exec.Command(executable, os.Getenv("AGENCY_TRANSPORT_ENTRYPOINT"))
		transport.Env = child.Env
		transport.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pgid: child.Process.Pid, Pdeathsig: syscall.SIGKILL}
		transport.Stdout, transport.Stderr = os.Stderr, os.Stderr
		if err := transport.Start(); err != nil {
			c.stopLocked()
			return errors.New("host credential transport failed to start")
		}
		c.transportDone = make(chan struct{})
		transportDone, childPID := c.transportDone, child.Process.Pid
		go func() {
			_ = transport.Wait()
			close(transportDone)
			_ = syscall.Kill(-childPID, syscall.SIGKILL)
		}()
	}
	for range 60 {
		if _, err := localRequest(ctx, "GET", "/readyz", nil); err == nil &&
			(c.transportDone == nil || transportReady(ctx, "http://127.0.0.1:18081/readyz")) {
			return nil
		}
		select {
		case <-ctx.Done():
			c.stopLocked()
			return ctx.Err()
		case <-done:
			c.stopLocked()
			return errors.New("coding process exited during startup")
		case <-time.After(time.Second):
		}
	}
	c.stopLocked()
	return errors.New("coding process did not become ready")
}

func transportReady(ctx context.Context, endpoint string) bool {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return false
	}
	response, err := (&http.Client{Timeout: time.Second}).Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode == http.StatusNoContent
}

func codingCommand() *exec.Cmd {
	return exec.Command(cmp.Or(os.Getenv("AGENCY_AGENT_EXECUTABLE"), os.Getenv("AGENCY_CODER_EXECUTABLE"), "/usr/local/bin/node"),
		cmp.Or(os.Getenv("AGENCY_AGENT_ENTRYPOINT"), os.Getenv("AGENCY_CODER_ENTRYPOINT"), "/app/coder/agent.mjs"))
}

func (c *codingRuntime) childEnvironment(inherited []string, directory, session string) []string {
	owned := map[string]string{
		"AGENT_DATA": directory, "AGENT_SESSION_ID": session,
		"AGENT_PORT": "18080", "AGENT_BIND": "127.0.0.1",
		"AGENT_COMPILED_CONFIG_PATH":  c.compiledConfigPath,
		"AGENT_RUNTIME_IDENTITY_PATH": apia2a.RuntimeIdentityPath,
		"AGENT_KAGENT_NAME":           c.agentName, "AGENT_KAGENT_NAMESPACE": c.agentNamespace,
	}
	result := make([]string, 0, len(inherited)+len(owned))
	for _, entry := range inherited {
		key, _, _ := strings.Cut(entry, "=")
		if _, replaced := owned[key]; !replaced && key != "KAGENT_CONFIG_JSON" {
			result = append(result, entry)
		}
	}
	for key, value := range owned {
		result = append(result, key+"="+value)
	}
	return result
}

func (c *codingRuntime) stop() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.stopLocked()
}

func (c *codingRuntime) stopLocked() {
	if c.child == nil {
		return
	}
	// Keep native ownership until the child is confirmed stopped; a timeout is
	// not proof that remote contributor work has ceased.
	for {
		err := syscall.Kill(-c.child.Process.Pid, syscall.SIGKILL)
		if err == nil || errors.Is(err, syscall.ESRCH) {
			select {
			case <-c.done:
				if c.transportDone != nil {
					<-c.transportDone
					c.transportDone = nil
				}
				c.child, c.done = nil, nil
				return
			case <-time.After(time.Second):
			}
		} else {
			time.Sleep(time.Second)
		}
	}
}

type codingHTTPError int

func (e codingHTTPError) Error() string { return fmt.Sprintf("coding endpoint returned %d", e) }

func localRequest(ctx context.Context, method, route string, body any) ([]byte, error) {
	var encoded []byte
	if body != nil {
		encoded, _ = json.Marshal(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, "http://127.0.0.1:18080"+route, bytes.NewReader(encoded))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	injectTraceContext(ctx, req)
	client := &http.Client{Timeout: 240 * time.Second}
	if method == "POST" {
		client.Timeout = 600 * time.Second
	}
	response, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return data, codingHTTPError(response.StatusCode)
	}
	return data, nil
}

func injectTraceContext(ctx context.Context, request *http.Request) {
	otel.GetTextMapPropagator().Inject(ctx, propagation.HeaderCarrier(request.Header))
}

func submitCoding(ctx context.Context, request any) error {
	for attempt := 0; attempt < 3; attempt++ {
		data, err := localRequest(ctx, "POST", "/tasks", request)
		if err == nil {
			return nil
		}
		var rejection struct {
			Error string `json:"error"`
		}
		// This response precedes Node task-journal creation. Unknown responses
		// and transport failures never permit replay.
		if err != codingHTTPError(http.StatusServiceUnavailable) ||
			json.Unmarshal(data, &rejection) != nil ||
			(rejection.Error != "agency_plugin_preflight_failed" &&
				rejection.Error != "repository_preflight_failed") || attempt == 2 {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	return errors.New("coding preflight unavailable")
}

func (c *codingRuntime) perform(ctx context.Context, input *a2asrv.ExecutorContext, text string) (a2a.TaskState, string, error) {
	if strings.HasPrefix(text, "fixture:") {
		select {
		case <-ctx.Done():
			return a2a.TaskStateCanceled, "fixture stopped", nil
		case <-time.After(15 * time.Second):
			result, _ := json.Marshal(map[string]any{"fixture": strings.TrimPrefix(text, "fixture:"),
				"session": input.ContextID, "completed": true})
			return a2a.TaskStateCompleted, string(result), nil
		}
	}
	var request map[string]any
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		return a2a.TaskStateFailed, "", errors.New("structured coding request required")
	}
	for key := range request {
		if !supportedCodingRequestField(key) {
			return a2a.TaskStateFailed, "", errors.New("unapproved coding request field")
		}
	}
	request["id"] = string(input.TaskID)
	if err := c.start(ctx, input.ContextID); err != nil {
		return a2a.TaskStateFailed, "", err
	}
	// Query first: repeated adapter delivery never blindly repeats a native turn.
	result, err := localRequest(ctx, "GET", "/tasks/"+string(input.TaskID), nil)
	if err != nil {
		if err != codingHTTPError(http.StatusNotFound) {
			c.stop()
			return a2a.TaskStateFailed, "existing coding outcome uncertain; no replay", err
		}
		if err := submitCoding(ctx, request); err != nil {
			c.stop()
			return a2a.TaskStateFailed, "submission uncertain; coding process stopped; no replay", err
		}
	}

	for {
		if ctx.Err() != nil {
			c.stop()
			return a2a.TaskStateCanceled, "coding process group stopped; remote writes are not rolled back", nil
		}
		pollCtx, cancel := context.WithTimeout(context.Background(), 240*time.Second)
		result, err = localRequest(pollCtx, "GET", "/tasks/"+string(input.TaskID), nil)
		cancel()
		if err != nil {
			c.stop()
			return a2a.TaskStateFailed, "coding process stopped; outcome requires inspection; no replay", err
		}
		var receipt struct {
			Status      string `json:"status"`
			ToolResults []struct {
				Operation string `json:"operation"`
				OK        *bool  `json:"ok"`
			} `json:"toolResults"`
		}
		if err := json.Unmarshal(result, &receipt); err != nil {
			c.stop()
			return a2a.TaskStateFailed, "", err
		}
		switch receipt.Status {
		case "completed":
			for _, tool := range receipt.ToolResults {
				if tool.Operation != "repo_validate" && tool.OK != nil && !*tool.OK {
					return a2a.TaskStateFailed, string(result), nil
				}
			}
			return a2a.TaskStateCompleted, string(result), nil
		case "failed", "interrupted":
			return a2a.TaskStateFailed, string(result), nil
		case "canceled":
			return a2a.TaskStateCanceled, string(result), nil
		}
		time.Sleep(time.Second)
	}
}

func supportedCodingRequestField(key string) bool {
	switch key {
	case "prompt", "allowEdits", "allowAuditUi", "refreshPlugins", "baseBranch", "workBranch",
		"workingDirectory", "nativeToolApprovals", "dryRun":
		return true
	default:
		return false
	}
}

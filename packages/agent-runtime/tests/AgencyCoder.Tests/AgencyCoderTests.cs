using AgencyCoder;
using Microsoft.Extensions.Options;
using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using System.Net;
using System.Net.Sockets;
using System.Net.Http.Json;
using Microsoft.Extensions.Configuration;

namespace AgencyCoder.Tests;

[TestClass]
public sealed class AgencyCoderTests
{
    [TestMethod]
    [DoNotParallelize]
    public async Task DryRunPreparesRepositoryAndAuthorizedPluginsWithoutStartingCopilot()
    {
        var directory = CreateDirectory();
        var previousSession = Environment.GetEnvironmentVariable("AGENT_SESSION_ID");
        Environment.SetEnvironmentVariable("AGENT_SESSION_ID", Guid.NewGuid().ToString());
        try
        {
            var source = Path.Combine(directory, "source");
            Directory.CreateDirectory(source);
            Directory.CreateDirectory(Path.Combine(source, "src", "component"));
            await File.WriteAllTextAsync(Path.Combine(source, "src", "component", "README.md"), "fixture");
            await WorkspaceManager.RunAsync("git", source, ["init", "-b", "main"], CancellationToken.None);
            await WorkspaceManager.RunAsync("git", source, ["add", "src/component/README.md"], CancellationToken.None);
            await WorkspaceManager.RunAsync("git", source,
                ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "fixture"],
                CancellationToken.None);
            var options = new HarnessOptions
            {
                DurableDirectory = Path.Combine(directory, "data"),
                RepositoryUrl = source,
                AgencyPolicyEndpoint = new("https://host.example.test/policy"),
                AgencyPluginsEndpoint = new("https://host.example.test/plugins"),
                CopilotExecutable = Path.Combine(directory, "must-never-start"),
            };
            using var http = new HttpClient(new PluginHandler(async request =>
            {
                Assert.AreEqual("Bearer kagent-credential-injected", request.Headers.Authorization?.ToString());
                if (request.RequestUri!.AbsolutePath == "/policy")
                    return new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = JsonContent.Create(new { profileSha256 = "profile" }),
                    };
                using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
                Assert.AreEqual("src/component", body.RootElement.GetProperty("workingDirectory").GetString());
                Assert.IsFalse(body.RootElement.TryGetProperty("configText", out _));
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = JsonContent.Create(new
                    {
                        manifest = PluginManifest("profile", body.RootElement.GetProperty("commit").GetString()!,
                            "src/component"),
                        bundles = Array.Empty<object>(),
                    }),
                };
            }));
            var bound = Options.Create(options);
            var native = LoadNative(directory);
            var journal = new TaskJournal(bound);
            var orchestrator = new HarnessOrchestrator(bound, native, journal, new WorkspaceManager(bound),
                new AgencyPluginResolver(bound, http), new CopilotExecutor(bound, native));
            await orchestrator.InitializeAsync(CancellationToken.None);
            var rejected = await orchestrator.SubmitAsync(new("unsafe", "prepare", DryRun: true), false,
                CancellationToken.None);
            Assert.AreEqual(400, rejected.StatusCode);
            var accepted = await orchestrator.SubmitAsync(new("dry-run", "prepare", AllowEdits: false,
                WorkingDirectory: "src/component", DryRun: true), false, CancellationToken.None);
            Assert.AreEqual(202, accepted.StatusCode);
            await WaitForAsync(() => orchestrator.Get("dry-run")?.Status == "completed");
            using var answer = JsonDocument.Parse(orchestrator.Get("dry-run")!.Answer!);
            Assert.IsTrue(answer.RootElement.GetProperty("policyAndPluginsAuthorized").GetBoolean());
            Assert.IsFalse(answer.RootElement.GetProperty("inferenceRequested").GetBoolean());
            Assert.AreEqual("src/component", answer.RootElement.GetProperty("workingDirectory").GetString());
            var changed = await orchestrator.SubmitAsync(new("dry-run", "prepare", AllowEdits: false,
                WorkingDirectory: "src/other"), false, CancellationToken.None);
            Assert.AreEqual(409, changed.StatusCode);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AGENT_SESSION_ID", previousSession);
            foreach (var file in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
                File.SetAttributes(file, FileAttributes.Normal);
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task PluginPreparationUsesProjectedCredentialForBothHostRequests()
    {
        var directory = CreateDirectory();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(directory, "agency.toml"), "test = true");
            await WorkspaceManager.RunAsync("git", directory, ["init"], CancellationToken.None);
            await WorkspaceManager.RunAsync("git", directory,
                ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-m", "fixture"],
                CancellationToken.None);
            var requests = 0;
            using var client = new HttpClient(new PluginHandler(async request =>
            {
                Assert.AreEqual("Bearer", request.Headers.Authorization?.Scheme);
                Assert.AreEqual("kagent-credential-injected", request.Headers.Authorization?.Parameter);
                requests++;
                if (request.RequestUri!.AbsolutePath == "/policy")
                    return new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = JsonContent.Create(new { profileSha256 = "profile" }),
                    };
                using var body = JsonDocument.Parse(await request.Content!.ReadAsStringAsync());
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = JsonContent.Create(new
                    {
                        manifest = PluginManifest("profile", body.RootElement.GetProperty("commit").GetString()!,
                            body.RootElement.GetProperty("workingDirectory").GetString()!),
                        bundles = Array.Empty<object>(),
                    }),
                };
            }));
            var resolver = new AgencyPluginResolver(Options.Create(CreateOptions(directory)), client);
            var result = await resolver.ResolveAsync(directory, directory, false, CancellationToken.None);
            Assert.AreEqual(new string('a', 64), result.Identity);
            Assert.AreEqual(2, requests);
            var refusals = 0;
            using var refusedClient = new HttpClient(new PluginHandler(_ =>
            {
                refusals++;
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized)
                {
                    Content = new StringContent("private-upstream-body"),
                });
            }));
            var refusedResolver = new AgencyPluginResolver(Options.Create(CreateOptions(directory)), refusedClient);
            var failure = await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
                refusedResolver.ResolveAsync(directory, directory, false, CancellationToken.None));
            Assert.AreEqual("host_policy_preparation_http_401", failure.Message);
            Assert.AreEqual(1, refusals);
        }
        finally
        {
            foreach (var file in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
                File.SetAttributes(file, FileAttributes.Normal);
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task PluginPreparationAcceptsDeclaredLayersAndRejectsForgedRepositorySources()
    {
        var directory = CreateDirectory();
        try
        {
            Directory.CreateDirectory(Path.Combine(directory, "src", "component"));
            await WorkspaceManager.RunAsync("git", directory, ["init", "-b", "main"], CancellationToken.None);
            await WorkspaceManager.RunAsync("git", directory,
                ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit",
                    "--allow-empty", "-m", "fixture"], CancellationToken.None);
            var executionDirectory = Path.Combine(directory, "src", "component");
            var commit = (await WorkspaceManager.RunAsync("git", directory, ["rev-parse", "HEAD"],
                CancellationToken.None)).Trim();
            var local = new
            {
                type = "file",
                path = "src/agency.yaml",
                format = "yaml",
                commit,
                sha256 = new string('c', 64),
            };
            var remote = new
            {
                type = "remote_config",
                declaredBy = "src/agency.yaml",
                git = "https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/shared-config",
                path = "agency.toml",
                format = "toml",
                resolvedRef = "d" + new string('0', 39),
                sha256 = new string('d', 64),
            };
            using var acceptedClient = new HttpClient(new PluginHandler(request =>
            {
                if (request.RequestUri!.AbsolutePath == "/policy")
                    return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = JsonContent.Create(new { profileSha256 = "profile" }),
                    });
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = JsonContent.Create(new
                    {
                        manifest = PluginManifest("profile", commit, "src/component", [remote, local]),
                        bundles = Array.Empty<object>(),
                    }),
                });
            }));
            var accepted = await new AgencyPluginResolver(Options.Create(CreateOptions(directory)),
                acceptedClient).ResolveAsync(directory, executionDirectory, false, CancellationToken.None);
            Assert.AreEqual(2, accepted.ConfigSourceCount);

            var forged = new
            {
                type = "file",
                path = "src/sibling/agency.toml",
                format = "toml",
                commit,
                sha256 = new string('e', 64),
            };
            using var rejectedClient = new HttpClient(new PluginHandler(request =>
            {
                if (request.RequestUri!.AbsolutePath == "/policy")
                    return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                    {
                        Content = JsonContent.Create(new { profileSha256 = "profile" }),
                    });
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = JsonContent.Create(new
                    {
                        manifest = PluginManifest("profile", commit, "src/component", [forged]),
                        bundles = Array.Empty<object>(),
                    }),
                });
            }));
            var rejected = await Assert.ThrowsExactlyAsync<InvalidOperationException>(() =>
                new AgencyPluginResolver(Options.Create(CreateOptions(directory)), rejectedClient)
                    .ResolveAsync(directory, executionDirectory, false, CancellationToken.None));
            Assert.AreEqual("plugin_manifest_mismatch", rejected.Message);
        }
        finally
        {
            foreach (var file in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
                File.SetAttributes(file, FileAttributes.Normal);
            Directory.Delete(directory, recursive: true);
        }
    }

    private sealed class PluginHandler(Func<HttpRequestMessage, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,
            CancellationToken cancellationToken) => send(request);
    }

    private static object PluginManifest(string profileSha256, string commit, string workingDirectory,
        object[]? configSources = null) => new
        {
            identity = new string('a', 64),
            policy = "test",
            profileSha256,
            repositoryCommit = commit,
            configResolution = "native-cwd-ancestor-v1",
            workingDirectory,
            configSourceCommit = commit,
            effectiveConfigSha256 = new string('b', 64),
            configSources = configSources ?? [],
        };

    private static string NativeConfig() => """
        {
          "runtime": {"name": "generic", "namespace": "sample"},
          "agent": {
            "instruction": "Read repository instructions.",
            "stream": true,
            "model": {
              "model": "mai-code-2",
              "openai": {"base_url": "https://model.example.test/v1", "api_format": "responses"},
              "headers": {"X-Profile": "host"}
            },
            "http_tools": [{
              "params": {
                "url": "https://agency.example.test/mcp",
                "headers": {"Authorization": "Bearer kagent-credential-injected"},
                "timeout": "12.5s",
                "terminate_on_close": true
              },
              "tools": ["call_tool"],
              "require_approval": true
            }]
          }
        }
        """;

    [TestMethod]
    public void NativeConfigMapsResponsesAndFailsClosedForChat()
    {
        var directory = CreateDirectory();
        try
        {
            var path = Path.Combine(directory, "config.json");
            File.WriteAllText(path, NativeConfig());
            var config = NativeConfigFrom(path);
            Assert.AreEqual("mai-code-2", config.Model);
            Assert.AreEqual("responses", config.WireApi);
            Assert.AreEqual("native-http-0", config.McpServers.Single().Name);
            File.WriteAllText(path, NativeConfig().Replace("\"responses\"", "\"chatCompletions\""));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                NativeConfigFrom(path));
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public void NativeConfigRejectsCredentialHeadersAndUnverifiedTls()
    {
        var directory = CreateDirectory();
        try
        {
            var path = Path.Combine(directory, "config.json");
            File.WriteAllText(path, NativeConfig().Replace("Bearer kagent-credential-injected", "Bearer private-secret"));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                NativeConfigFrom(path));
            File.WriteAllText(path, NativeConfig().Replace("\"headers\": {\"X-Profile\": \"host\"}",
                "\"headers\": {\"X-Profile\": \"host\"}, \"tls_insecure_skip_verify\": true"));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                NativeConfigFrom(path));
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public void NativeConfigRejectsUnsupportedSseWithoutTranslation()
    {
        var directory = CreateDirectory();
        try
        {
            var path = Path.Combine(directory, "config.json");
            var config = NativeConfig().Replace("""
                  }
                }
                """, """
                    ,
                    "sse_tools": [{
                      "params": {"url": "https://tools.example.test/sse"},
                      "tools": ["lookup"]
                    }]
                  }
                }
                """);
            File.WriteAllText(path, config);
            var error = Assert.ThrowsExactly<InvalidOperationException>(() => NativeConfigFrom(path));
            StringAssert.Contains(error.Message, "SSE");
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public void NativeConfigRejectsUnknownFieldsThroughOptionsValidation()
    {
        var directory = CreateDirectory();
        try
        {
            var path = Path.Combine(directory, "config.json");
            File.WriteAllText(path, NativeConfig().Replace("\"stream\": true,", """
                "stream": true,
                "unexpected": true,
                """));
            var error = Assert.ThrowsExactly<InvalidOperationException>(() => NativeConfigFrom(path));
            StringAssert.Contains(error.Message, "unexpected");
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task RecoveryInterruptsUnsettledTasksWithoutReplayingSettledTasks()
    {
        var directory = CreateDirectory();
        try
        {
            var journal = new TaskJournal(Microsoft.Extensions.Options.Options.Create(
                new HarnessOptions { DurableDirectory = directory }));
            await journal.SaveAsync(MakeTask("accepted", "accepted"), CancellationToken.None);
            await journal.SaveAsync(MakeTask("completed", "completed"), CancellationToken.None);
            var recovered = await journal.RecoverAsync(CancellationToken.None);
            Assert.AreEqual("interrupted", recovered["accepted"].Status);
            Assert.AreEqual("completed", recovered["completed"].Status);
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public void WorkspaceConfinementRejectsTraversalAndGitMetadata()
    {
        var root = CreateDirectory();
        try
        {
            var nested = Path.Combine(root, "src", "component");
            Directory.CreateDirectory(nested);
            Assert.IsTrue(WorkspaceManager.Confined(root, "source/Program.cs"));
            Assert.IsFalse(WorkspaceManager.Confined(root, "../outside.txt"));
            Assert.IsFalse(WorkspaceManager.Confined(root, ".git/config"));
            Assert.IsFalse(WorkspaceManager.Confined(root, ".GIT/config"));
            Assert.AreEqual(nested, WorkspaceManager.ResolveExecutionDirectory(root, "src/component"));
            Assert.AreEqual(root, WorkspaceManager.ResolveExecutionDirectory(root, null));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                WorkspaceManager.ResolveExecutionDirectory(root, "../outside"));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                WorkspaceManager.ResolveExecutionDirectory(root, "src."));
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                WorkspaceManager.ResolveExecutionDirectory(root, "src/missing"));
            Assert.IsTrue(WorkspaceManager.SafeBranch("users/example/work"));
            Assert.IsFalse(WorkspaceManager.SafeWorkBranch("main"));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void RedactionRemovesCredentialsWithoutReturningInputs()
    {
        var value = NativeHarnessConfig.Redact("token=private-secret; bearer private-secret", ["private-secret"]);
        Assert.IsFalse(value.Contains("private-secret", StringComparison.Ordinal));
        StringAssert.Contains(value, "[REDACTED]");
    }

    [TestMethod]
    public async Task ExecutionFailureReleasesAdmissionForAnotherTask()
    {
        var directory = CreateDirectory();
        var oldSession = Environment.GetEnvironmentVariable("AGENT_SESSION_ID");
        Environment.SetEnvironmentVariable("AGENT_SESSION_ID", Guid.NewGuid().ToString());
        var attempts = 0;
        try
        {
            var native = LoadNative(directory);
            File.WriteAllText(Path.Combine(directory, "archive"), "block archive directory");
            var options = CreateOptions(directory);
            var journal = new TaskJournal(Microsoft.Extensions.Options.Options.Create(options));
            var orchestrator = new HarnessOrchestrator(options, native, journal, null, null, null,
                await journal.RecoverAsync(CancellationToken.None),
                (_, _, _) =>
                {
                    attempts++;
                    return System.Threading.Tasks.Task.FromException<string>(
                        new InvalidOperationException("controlled_failure"));
                });
            var first = await orchestrator.SubmitAsync(new("first", "inspect"), false, CancellationToken.None);
            await WaitForAsync(() => orchestrator.Get("first")?.Status == "failed");
            var second = await orchestrator.SubmitAsync(new("second", "inspect"), false, CancellationToken.None);
            Assert.AreEqual(202, first.StatusCode);
            Assert.AreEqual(202, second.StatusCode);
            Assert.AreEqual("failed", orchestrator.Get("first")?.Status);
            await WaitForAsync(() => attempts == 2);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AGENT_SESSION_ID", oldSession);
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task InitialJournalFailureReleasesAdmissionWithoutPersistingOwnership()
    {
        var directory = CreateDirectory();
        var oldSession = Environment.GetEnvironmentVariable("AGENT_SESSION_ID");
        Environment.SetEnvironmentVariable("AGENT_SESSION_ID", Guid.NewGuid().ToString());
        try
        {
            var options = CreateOptions(directory);
            var journal = new TaskJournal(Microsoft.Extensions.Options.Options.Create(options));
            var native = LoadNative(directory);
            var orchestrator = new HarnessOrchestrator(options, native, journal, null, null, null,
                new Dictionary<string, CodingTask>(),
                (_, _, _) => System.Threading.Tasks.Task.FromResult("answer"));
            Directory.CreateDirectory(Path.Combine(directory, "tasks", "blocked.json"));
            Exception? failure = null;
            try
            {
                await orchestrator.SubmitAsync(new("blocked", "inspect"), false, CancellationToken.None);
            }
            catch (Exception error)
            {
                failure = error;
            }
            Assert.IsTrue(failure is IOException or UnauthorizedAccessException);
            Assert.IsNull(orchestrator.Get("blocked"));

            Directory.Delete(Path.Combine(directory, "tasks", "blocked.json"));
            var admitted = await orchestrator.SubmitAsync(new("next", "inspect"), false, CancellationToken.None);
            Assert.AreEqual(202, admitted.StatusCode);
            await WaitForAsync(() => orchestrator.Get("next")?.ArchivePath is not null
                && !Directory.EnumerateFiles(Path.Combine(directory, "tasks"), "next.json.*.partial").Any());
        }
        finally
        {
            Environment.SetEnvironmentVariable("AGENT_SESSION_ID", oldSession);
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task CancelledProcessTreeIsObservedBeforeCommandReturns()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.CancelAfter(200);
        var stopwatch = Stopwatch.StartNew();
        OperationCanceledException? exception = null;
        try
        {
            await WorkspaceManager.RunAsync("powershell.exe", Path.GetFullPath("."),
                ["-NoProfile", "-Command", "Start-Sleep 30"], cancellation.Token);
        }
        catch (OperationCanceledException error)
        {
            exception = error;
        }
        Assert.IsTrue(stopwatch.Elapsed < TimeSpan.FromSeconds(10));
        Assert.IsNotNull(exception);
    }

    [TestMethod]
    public void PluginExtractionAcceptsExplicitDirectoriesAndRejectsTraversal()
    {
        var directory = CreateDirectory();
        try
        {
            var nested = Path.Combine(directory, "nested.zip");
            using (var zip = ZipFile.Open(nested, ZipArchiveMode.Create))
            {
                zip.CreateEntry("skills/");
                var entry = zip.CreateEntry("skills/example/SKILL.md");
                using var writer = new StreamWriter(entry.Open());
                writer.Write("skill");
            }
            var destination = Path.Combine(directory, "bundle");
            AgencyPluginResolver.Extract(File.ReadAllBytes(nested), destination);
            Assert.AreEqual("skill", File.ReadAllText(Path.Combine(destination, "skills", "example", "SKILL.md")));

            var traversal = Path.Combine(directory, "traversal.zip");
            using (var zip = ZipFile.Open(traversal, ZipArchiveMode.Create))
                zip.CreateEntry("../escape.txt");
            Assert.ThrowsExactly<InvalidOperationException>(() =>
                AgencyPluginResolver.Extract(File.ReadAllBytes(traversal), Path.Combine(directory, "bad")));
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public void TaskRequestContractRejectsUnknownFields()
    {
        Assert.ThrowsExactly<JsonException>(() => JsonSerializer.Deserialize<CodingTaskRequest>(
            """{"id":"task","prompt":"test","repositoryUrl":"unapproved"}""", TaskJournal.Json));
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task MinimalApiEnforcesTaskContractAndRequestLimit()
    {
        var directory = CreateDirectory();
        var config = LoadNative(directory);
        var port = AvailablePort();
        var previous = new Dictionary<string, string?>
        {
            ["AGENT_NATIVE_CONFIG"] = "true",
            ["AGENT_COMPILED_CONFIG_PATH"] = Path.Combine(directory, "config.json"),
            ["AGENT_DATA"] = directory,
            ["AGENT_SESSION_ID"] = Guid.NewGuid().ToString(),
            ["AGENT_PORT"] = port.ToString(),
            ["AGENCY_REPOSITORY_URL"] = "https://repo.example.test/code",
            ["AGENCY_POLICY_URL"] = "https://host.example.test/policy",
            ["AGENCY_PLUGINS_URL"] = "https://host.example.test/plugins",
            ["OPENAI_API_KEY"] = "kagent-credential-injected",
            ["AGENCY_COPILOT_EXECUTABLE"] = "agency",
            ["AGENCY_TASK_TIMEOUT"] = "00:00:05",
            ["NATIVE_MEMORY_ENABLED"] = null,
            ["NATIVE_MEMORY_CONFIG"] = null,
            ["NATIVE_MEMORY_CONFIG_JSON"] = null,
            ["SESSION_ARCHIVE_EXPORT_ENABLED"] = null,
            ["NODE_TLS_REJECT_UNAUTHORIZED"] = null,
            ["AGENT_RUNTIME_IDENTITY_PATH"] = null,
        }.ToDictionary(pair => pair.Key,
            pair => (Before: Environment.GetEnvironmentVariable(pair.Key), After: pair.Value));
        foreach (var (name, value) in previous)
            Environment.SetEnvironmentVariable(name, value.After);
        await using var app = HarnessApplication.Build([]);
        try
        {
            await app.StartAsync();
            using var http = new HttpClient { BaseAddress = new($"http://127.0.0.1:{port}") };
            using var unknown = await http.PostAsync("/tasks", JsonContent.Create(
                new { id = "bad", prompt = "test", repositoryUrl = "unapproved" }));
            Assert.AreEqual(HttpStatusCode.BadRequest, unknown.StatusCode);
            using var large = await http.PostAsync("/tasks", new StringContent(new string('x', 20_001),
                System.Text.Encoding.UTF8, "application/json"));
            Assert.AreEqual(HttpStatusCode.RequestEntityTooLarge, large.StatusCode);

            var tasks = new List<Activity>();
            using var listener = new ActivityListener
            {
                ShouldListenTo = source => source.Name == AgencyCoderTracing.Name,
                Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
                ActivityStopped = activity => tasks.Add(activity),
            };
            ActivitySource.AddActivityListener(listener);
            var parents = new[]
            {
                "00-11111111111111111111111111111111-1111111111111111-01",
                "00-22222222222222222222222222222222-2222222222222222-01",
            };
            for (var index = 0; index < parents.Length; index++)
            {
                using var caller = new Activity("adapter-test").SetParentId(parents[index])
                    .AddBaggage("tenant", $"test-{index}").Start();
                using var request = new HttpRequestMessage(HttpMethod.Post, "/tasks");
                request.Content = JsonContent.Create(new { id = $"trace-{index}", prompt = "test" });
                using var response = await http.SendAsync(request);
                Assert.AreEqual(HttpStatusCode.Accepted, response.StatusCode);
                await WaitForAsync(() => tasks.Any(activity => activity.GetTagItem("agency_coder.task_id")?.ToString()
                    == $"trace-{index}"));
            }
            var taskActivities = tasks.Where(activity => activity.OperationName == "agency-coder.task")
                .ToDictionary(activity => activity.GetTagItem("agency_coder.task_id")!.ToString()!);
            for (var index = 0; index < parents.Length; index++)
            {
                var activity = taskActivities[$"trace-{index}"];
                Assert.AreEqual(parents[index].Substring(3, 32), activity.TraceId.ToHexString());
                Assert.AreEqual($"test-{index}", activity.GetBaggageItem("tenant"));
            }
        }
        finally
        {
            await app.StopAsync();
            foreach (var (name, value) in previous)
                Environment.SetEnvironmentVariable(name, value.Before);
            Directory.Delete(directory, recursive: true);
        }
    }

    [TestMethod]
    public async Task DuplicateAdmissionReturnsExistingTaskAndRejectsChangedRequest()
    {
        var directory = CreateDirectory();
        var path = Path.Combine(directory, "config.json");
        File.WriteAllText(path, NativeConfig());
        var native = NativeConfigFrom(path);
        var request = new CodingTaskRequest("task-1", "inspect");
        var existing = MakeTask(request.Id, "completed",
            HarnessOrchestrator.RequestDigest(request, false, native.ConfigSha256));
        try
        {
            var options = CreateOptions(directory);
            var journal = new TaskJournal(Options.Create(options));
            await journal.SaveAsync(existing, CancellationToken.None);
            var orchestrator = new HarnessOrchestrator(options, native, journal, null, null, null,
                await journal.RecoverAsync(CancellationToken.None), (_, _, _) => throw new InvalidOperationException());
            var duplicate = await orchestrator.SubmitAsync(request, false, CancellationToken.None);
            var changed = await orchestrator.SubmitAsync(request with { Prompt = "change" }, false,
                CancellationToken.None);
            var explicitRoot = await orchestrator.SubmitAsync(request with { WorkingDirectory = "." }, false,
                CancellationToken.None);
            var changedScope = await orchestrator.SubmitAsync(request with { WorkingDirectory = "src/component" }, false,
                CancellationToken.None);
            Assert.AreEqual(200, duplicate.StatusCode);
            Assert.AreEqual(existing.Id, duplicate.Task?.Id);
            Assert.AreEqual(409, changed.StatusCode);
            Assert.AreEqual("task_identity_conflict", changed.Error);
            Assert.AreEqual(200, explicitRoot.StatusCode);
            Assert.AreEqual(409, changedScope.StatusCode);
            Assert.AreEqual("task_identity_conflict", changedScope.Error);
        }
        finally
        {
            Directory.Delete(directory, recursive: true);
        }
    }

    private static string CreateDirectory()
    {
        var path = Path.Combine(Path.GetFullPath("."), ".agency-coder-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }

    private static CodingTask MakeTask(string id, string status, string? requestSha256 = null) => new()
    {
        Id = id,
        Prompt = "test",
        RequestSha256 = requestSha256 ?? "sha",
        SessionId = Guid.NewGuid().ToString(),
        BaseBranch = "main",
        AcceptedAt = DateTimeOffset.UtcNow,
        Status = status,
    };

    private static HarnessOptions CreateOptions(string directory) => new()
    {
        AgencyPolicyEndpoint = new("https://host.example.test/policy"),
        AgencyPluginsEndpoint = new("https://host.example.test/plugins"),
        DurableDirectory = directory,
        RepositoryUrl = "https://repo.example.test/code",
        WorkspaceName = "repository",
        CopilotExecutable = "agency",
        Port = 18080,
        TaskTimeout = TimeSpan.FromSeconds(1),
    };

    private static NativeHarnessConfig LoadNative(string directory)
    {
        var path = Path.Combine(directory, "config.json");
        File.WriteAllText(path, NativeConfig());
        return NativeConfigFrom(path);
    }

    private static NativeHarnessConfig NativeConfigFrom(string path)
    {
        var configuration = new ConfigurationBuilder().AddJsonFile(path)
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["AGENT_COMPILED_CONFIG_PATH"] = path,
            }).Build();
        var agent = configuration.GetSection("agent").Get<NativeAgentOptions>()!;
        var validation = new NativeOptionsValidator(configuration).Validate(null, agent);
        if (validation.Failed)
            throw new InvalidOperationException(string.Join(" ", validation.Failures ?? []));
        return NativeHarnessConfig.Create(agent, "kagent-credential-injected", configuration);
    }

    private static async Task WaitForAsync(Func<bool> predicate)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!predicate())
            await Task.Delay(20, timeout.Token);
    }

    private static int AvailablePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }
}

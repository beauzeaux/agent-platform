using Grpc.Core;
using HostCapabilities.NativeAdo.Probe;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace NativeAdo.Tests;

[TestClass]
public sealed class ProbeTests
{
    [TestMethod]
    public async Task ProbePrintsOnlyLengthOrClassifiedStatusNeverExceptionDetailsOrTrailers()
    {
        using var output = new StringWriter();
        Assert.AreEqual(0, await ProbeRunner.RunAsync(_ => Task.FromResult(17), output, TimeSpan.FromSeconds(1)));
        Assert.AreEqual("success=true length=17 status=OK" + Environment.NewLine, output.ToString());
        output.GetStringBuilder().Clear();
        Assert.AreEqual(1, await ProbeRunner.RunAsync(_ => throw new RpcException(
            new Status(StatusCode.PermissionDenied, "fake-sensitive-message"),
            new Metadata { { "sensitive-trailer", "fake-token-never-print" } }), output, TimeSpan.FromSeconds(1)));
        Assert.AreEqual("success=false length=0 status=PermissionDenied" + Environment.NewLine, output.ToString());
    }

    [TestMethod]
    public async Task ProbeHasABoundedDeadlineAndClassifiesBadConfiguration()
    {
        using var output = new StringWriter();
        Assert.AreEqual(1, await ProbeRunner.RunAsync(async ct =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return 0;
        }, output, TimeSpan.FromMilliseconds(20)));
        Assert.AreEqual("success=false length=0 status=DeadlineExceeded" + Environment.NewLine, output.ToString());
        output.GetStringBuilder().Clear();
        Assert.AreEqual(1, await ProbeRunner.RunAsync(_ => throw new ArgumentException("fake-sensitive-path"),
            output, TimeSpan.FromSeconds(1)));
        Assert.AreEqual("success=false length=0 status=InvalidConfiguration" + Environment.NewLine, output.ToString());
    }
}

using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.Text.Json;

namespace AdoDevProxy;

internal sealed class UserState
{
    private readonly object accountsLock = new();
    internal string DirectoryPath { get; }
    internal string PublicCertificatePath => Path.Combine(DirectoryPath, "root.crt");
    private string RootPath => Path.Combine(DirectoryPath, "root.dpapi");
    private string AccountsPath => Path.Combine(DirectoryPath, "accounts.json");

    internal UserState(string path)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Windows host required.");
        DirectoryPath = Path.GetFullPath(path);
        var info = Directory.CreateDirectory(DirectoryPath);
        var access = new DirectorySecurity();
        var user = WindowsIdentity.GetCurrent().User!;
        access.SetOwner(user);
        access.SetAccessRuleProtection(true, false);
        access.AddAccessRule(new FileSystemAccessRule(user, FileSystemRights.FullControl,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        info.SetAccessControl(access);
    }

    internal FileStream AcquireLock() =>
        new(Path.Combine(DirectoryPath, "process.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);

    internal X509Certificate2 InitializeCertificate(bool rotate)
    {
        if (File.Exists(RootPath) && !rotate)
            throw new InvalidOperationException("CA already exists; use rotate-ca explicitly.");
        using var key = RSA.Create(3072);
        var request = new CertificateRequest("CN=AdoDevProxy Local Interception " + Guid.NewGuid().ToString("N"),
            key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, true, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, false));
        using var root = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddYears(1));
        var bytes = root.Export(X509ContentType.Pfx);
        try { AtomicWrite(RootPath, ProtectedData.Protect(bytes, null, DataProtectionScope.CurrentUser)); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
        AtomicWrite(PublicCertificatePath, System.Text.Encoding.ASCII.GetBytes(root.ExportCertificatePem()));
        return LoadCertificate();
    }

    internal X509Certificate2 LoadCertificate()
    {
        if (!File.Exists(RootPath)) throw new InvalidOperationException("Run init-ca first.");
        var bytes = ProtectedData.Unprotect(File.ReadAllBytes(RootPath), null, DataProtectionScope.CurrentUser);
        try
        {
            var root = X509CertificateLoader.LoadPkcs12(bytes, null, X509KeyStorageFlags.EphemeralKeySet | X509KeyStorageFlags.Exportable);
            if (!root.HasPrivateKey || root.NotAfter.ToUniversalTime() < DateTime.UtcNow.AddDays(2))
            {
                root.Dispose();
                throw new InvalidOperationException("CA expired or unusable; rotate it and update container trust.");
            }
            return root;
        }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }

    internal string? Account(string identity)
    {
        lock (accountsLock) return ReadAccounts().GetValueOrDefault(identity);
    }

    internal void SaveAccount(string identity, string account)
    {
        lock (accountsLock)
        {
            var accounts = ReadAccounts();
            if (accounts.GetValueOrDefault(identity) == account) return;
            accounts[identity] = account;
            AtomicWrite(AccountsPath, JsonSerializer.SerializeToUtf8Bytes(accounts));
        }
    }

    private Dictionary<string, string> ReadAccounts() =>
        File.Exists(AccountsPath)
            ? new(JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(AccountsPath))
                ?? throw new InvalidOperationException("Invalid selected-account state."), StringComparer.OrdinalIgnoreCase)
            : new(StringComparer.OrdinalIgnoreCase);

    private static void AtomicWrite(string path, byte[] bytes)
    {
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllBytes(temporary, bytes);
            File.Move(temporary, path, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}

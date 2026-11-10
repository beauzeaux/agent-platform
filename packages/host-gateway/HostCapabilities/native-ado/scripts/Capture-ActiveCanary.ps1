[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$moduleRoot = Split-Path $PSScriptRoot -Parent
$output = Join-Path $moduleRoot 'artifacts\live-canary'
New-Item -ItemType Directory -Force $output | Out-Null

function Get-PublicResource {
    param([string[]] $ResourceArguments)
    $result = & wsl -d Ubuntu-26.04 -- /snap/bin/microk8s kubectl get @ResourceArguments -o json --request-timeout=20s
    if ($LASTEXITCODE -ne 0) { throw 'Read-only cluster inspection failed.' }
    return ($result -join "`n" | ConvertFrom-Json -AsHashtable)
}

$config = Get-PublicResource @('-n', 'ate-system', 'configmap', 'atenet-egress-agentgateway-config')
$deployment = Get-PublicResource @('-n', 'ate-system', 'deployment', 'atenet-egress')
$provider = Get-PublicResource @('-n', 'ate-system', 'deployment', 'k8s-credential-provider')
$bundles = Get-PublicResource @('clustertrustbundles', '-l', 'podcert.ate.dev/canarying=live')
$roots = @($bundles.items | Where-Object { $_.spec.signerName -eq 'podidentity.podcert.ate.dev/identity' })
$expectedIdentity = 'spiffe://cluster.local/ns/ate-system/sa/atenet-egress'
if ($roots.Count -eq 0 -or $deployment.spec.template.spec.serviceAccountName -ne 'atenet-egress' -or
    $provider.spec.template.spec.containers[0].args -notcontains "--injector-identity=$expectedIdentity") {
    throw 'Gateway identity or public issuer inspection did not match the primary deployment.'
}
$podIdentity = @($deployment.spec.template.spec.volumes | Where-Object { $_.name -eq 'podidentity' })
if ($podIdentity.Count -ne 1 -or
    $podIdentity[0].projected.sources[0].podCertificate.signerName -ne 'podidentity.podcert.ate.dev/identity') {
    throw 'Unexpected podidentity projection.'
}

$pem = ($roots | ForEach-Object { $_.spec.trustBundle }) -join "`n"
if ($pem.Contains('PRIVATE KEY') -or !$pem.Contains('-----BEGIN CERTIFICATE-----')) {
    throw 'Expected public trust anchors only.'
}
Set-Content -LiteralPath (Join-Path $output 'podidentity-public-roots.pem') -Value $pem -Encoding utf8NoBOM
$certificates = [System.Security.Cryptography.X509Certificates.X509Certificate2Collection]::new()
$certificates.ImportFromPem($pem)
try {
    $certificateInfo = @($certificates | ForEach-Object {
        $publicCertificate = $_
        $chain = [System.Security.Cryptography.X509Certificates.X509Chain]::new()
        try {
            $chain.ChainPolicy.TrustMode = [System.Security.Cryptography.X509Certificates.X509ChainTrustMode]::CustomRootTrust
            $chain.ChainPolicy.CustomTrustStore.Add($publicCertificate) | Out-Null
            $chain.ChainPolicy.DisableCertificateDownloads = $true
            $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
            try {
                $rootOnlyStatus = if ($chain.Build($publicCertificate)) { 'Accepted' } else { 'Rejected' }
            } catch {
                $rootOnlyStatus = 'PlatformChainError'
            }
        } finally {
            $chain.Dispose()
        }
        @{
            sha256 = $publicCertificate.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256)
            subject = $publicCertificate.Subject
            publicKeyOid = $publicCertificate.PublicKey.Oid.Value
            signatureOid = $publicCertificate.SignatureAlgorithm.Value
            notAfterUtc = $publicCertificate.NotAfter.ToUniversalTime().ToString('o')
            windowsPublicRootOnlyChainCheck = $rootOnlyStatus
            windowsRuntime = [Environment]::Version.ToString()
        }
    })
} finally {
    foreach ($certificate in $certificates) { $certificate.Dispose() }
}
$snapshot = @{ configMap = $config; deployment = $deployment }
$snapshot | ConvertTo-Json -Depth 100 |
    Set-Content -LiteralPath (Join-Path $output 'active-snapshot.json') -Encoding utf8NoBOM
$evidence = @{
    capturedUtc = [DateTime]::UtcNow.ToString('o')
    gatewaySpiffeId = $expectedIdentity
    deploymentUid = $deployment.metadata.uid
    deploymentResourceVersion = $deployment.metadata.resourceVersion
    deploymentGeneration = $deployment.metadata.generation
    configMapUid = $config.metadata.uid
    configMapResourceVersion = $config.metadata.resourceVersion
    image = $deployment.spec.template.spec.containers[0].image
    publicTrustBundleRefs = @($roots | ForEach-Object {
        @{ name = $_.metadata.name; resourceVersion = $_.metadata.resourceVersion; signerName = $_.spec.signerName }
    })
    certificates = $certificateInfo
}
$evidence | ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath (Join-Path $output 'public-evidence.json') -Encoding utf8NoBOM
Write-Output 'Captured public gateway configuration and public issuer roots only. No apply, restart, or credential read.'
